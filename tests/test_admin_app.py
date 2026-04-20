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
    assert captured["runtime_dir"] == Path(admin_app.__file__).resolve().parents[2] / "runtime"


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
    assert admin_app._config_field_kind("host_heartbeat_pr_resume_prompt") == ("multiline_text", ())
    assert admin_app._config_field_kind("host_heartbeat_discord_resume_prompt") == ("multiline_text", ())
    assert admin_app._config_field_kind("pr_template_markdown") == ("multiline_text", ())
    assert admin_app._config_field_kind("host_resume_script_path") == ("text", ())


def test_config_field_kind_supports_issue_setup_fields() -> None:
    assert admin_app._config_field_kind("git_root") == ("text", ())
    assert admin_app._config_field_kind("default_model") == ("text", ())
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
    assert admin_app._config_field_kind("host_heartbeat_scheduled_wake_prefix_text") == ("text", ())


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


def test_list_wake_schedules_for_selected_directory_aggregates_and_sorts() -> None:
    window = SimpleNamespace(
        _selected_directory_path="root/team-a",
        service=SimpleNamespace(
            list_sessions=lambda **kwargs: [SimpleNamespace(session_id="s1"), SimpleNamespace(session_id="s2")],
            list_wake_schedules=lambda session_id: [
                {
                    "request_id": f"wake-{session_id}",
                    "updated_at": "2026-04-01T10:00:00Z" if session_id == "s1" else "2026-04-01T11:00:00Z",
                }
            ],
        ),
    )

    schedules = admin_app.MainWindow.list_wake_schedules_for_selected_directory(window)

    assert [item["request_id"] for item in schedules] == ["wake-s2", "wake-s1"]


def test_create_wake_schedule_uses_admin_ui_source() -> None:
    captured: dict[str, object] = {}
    window = SimpleNamespace(
        service=SimpleNamespace(create_wake_schedule=object()),
        run_action=lambda label, fn, **kwargs: captured.update({"label": label, "fn": fn, "kwargs": kwargs}),
    )

    admin_app.MainWindow.create_wake_schedule(
        window,
        session_id="session-1",
        prompt="Wake up",
        cron="*/10 * * * *",
        resume_mode="same_context",
    )

    assert captured["label"] == "Create wake schedule for session-1"
    assert captured["kwargs"]["source"] == "admin_ui"


def test_directory_config_domain_help_text_calls_out_communications_fields() -> None:
    communications_help = admin_app._directory_config_domain_help_text("communications")

    assert communications_help is not None
    assert "Messaging integrations for Discord and Teams" in communications_help
    assert admin_app._directory_config_domain_help_text("access_policy") is not None
    assert admin_app._directory_config_domain_help_text("providers") is None
    assert admin_app._directory_config_domain_help_text("email") is None


def test_directory_config_domain_help_text_legacy_tab_names_return_none() -> None:
    assert admin_app._directory_config_domain_help_text("discord") is None
    assert admin_app._directory_config_domain_help_text("teams") is None


def test_directory_config_tab_field_sources_omit_discord_fields_from_access_policy_and_providers_tabs() -> None:
    policy_fields = {
        field_name
        for _domain, field_name in admin_app._directory_config_tab_field_sources("access_policy")
    }
    provider_fields = {
        field_name
        for _domain, field_name in admin_app._directory_config_tab_field_sources("providers")
    }

    assert "discord_operator_user_allowlist" not in policy_fields
    assert "discord_operator_channel_allowlist" not in policy_fields
    assert "discord_backend" not in provider_fields
    assert "discord_bot_token_env" not in provider_fields


def test_directory_config_tab_field_sources_expose_composite_communications_tab() -> None:
    field_sources = admin_app._directory_config_tab_field_sources("communications")
    field_domains = {field_name: domain for domain, field_name in field_sources}

    assert ("providers", "discord_backend") in field_sources
    assert ("providers", "discord_read_reaction_emoji") in field_sources
    assert ("policy", "discord_operator_user_allowlist") in field_sources
    assert field_domains["discord_bot_token_env"] == "providers"
    assert field_domains["discord_read_reaction_emoji"] == "providers"
    assert field_domains["discord_poll_operator_dms"] == "policy"
    assert admin_app._directory_config_tab_underlying_domains("communications") == ("providers", "policy")


def test_directory_config_tab_field_sources_include_teams_mixed_scope_in_communications_tab() -> None:
    field_sources = admin_app._directory_config_tab_field_sources("communications")
    field_domains = {field_name: domain for domain, field_name in field_sources}

    assert ("providers", "teams_backend") in field_sources
    assert ("providers", "teams_graph_api_base_url") in field_sources
    assert ("worker_global:teams-ingest", "poll_interval_seconds") in field_sources
    assert ("worker_global:teams-ingest", "directories") in field_sources
    assert field_domains["teams_backend"] == "providers"
    assert field_domains["poll_interval_seconds"] == "worker_global:teams-ingest"
    assert admin_app._directory_config_tab_underlying_domains("communications") == ("providers", "policy")
    assert admin_app._directory_config_tab_worker_global_sources("communications") == ("teams-ingest",)


def test_directory_config_legacy_redirects_point_to_communications_tab() -> None:
    discord_redirect = admin_app._directory_config_legacy_redirect("discord")
    teams_redirect = admin_app._directory_config_legacy_redirect("teams")

    assert discord_redirect is not None
    assert discord_redirect["new_key"] == "communications"
    assert "non-blocking migration hint" in discord_redirect["message"]
    assert teams_redirect is not None
    assert teams_redirect["new_key"] == "communications"
    assert admin_app._directory_config_legacy_redirect("communications") is None


def test_collect_config_form_payloads_by_source_splits_discord_tab_fields() -> None:
    class DummyCheckbox:
        def __init__(self, checked: bool) -> None:
            self._checked = checked

        def isChecked(self) -> bool:
            return self._checked

    window = SimpleNamespace(
        _selected_directory_path="root/team-a",
        _config_widgets={
            "communications": {
                "field_specs": {
                    "discord_backend": {
                        "include_checkbox": DummyCheckbox(True),
                        "clear_checkbox": DummyCheckbox(False),
                        "kind": "literal",
                        "control": object(),
                    },
                    "discord_operator_user_allowlist": {
                        "include_checkbox": DummyCheckbox(True),
                        "clear_checkbox": DummyCheckbox(False),
                        "kind": "string_list",
                        "control": object(),
                    },
                },
                "field_domains": {
                    "discord_backend": "providers",
                    "discord_operator_user_allowlist": "policy",
                },
            }
        },
        _read_config_field_control_value=lambda spec: (
            "bot_api" if spec["kind"] == "literal" else ["123456789012345678"]
        ),
        _collect_config_form_payload=lambda domain: admin_app.MainWindow._collect_config_form_payload(window, domain),
    )

    payloads = admin_app.MainWindow._collect_config_form_payloads_by_source(window, "communications")

    assert payloads == {
        "providers": {"discord_backend": "bot_api"},
        "policy": {"discord_operator_user_allowlist": ["123456789012345678"]},
    }


def test_collect_config_form_payloads_by_source_filters_scope() -> None:
    class DummyCheckbox:
        def __init__(self, checked: bool) -> None:
            self._checked = checked

        def isChecked(self) -> bool:
            return self._checked

    window = SimpleNamespace(
        _selected_directory_path="root/team-a",
        _config_widgets={
            "communications": {
                "field_specs": {
                    "teams_backend": {
                        "include_checkbox": DummyCheckbox(True),
                        "clear_checkbox": DummyCheckbox(False),
                        "kind": "literal",
                        "control": object(),
                    },
                    "poll_interval_seconds": {
                        "include_checkbox": DummyCheckbox(True),
                        "clear_checkbox": DummyCheckbox(False),
                        "kind": "float",
                        "control": object(),
                    },
                },
                "field_domains": {
                    "teams_backend": "providers",
                    "poll_interval_seconds": "worker_global:teams-ingest",
                },
            }
        },
        _read_config_field_control_value=lambda spec: (
            "graph_api" if spec["kind"] == "literal" else 45.0
        ),
        _collect_config_form_payload=lambda domain: admin_app.MainWindow._collect_config_form_payload(window, domain),
    )

    directory_payloads = admin_app.MainWindow._collect_config_form_payloads_by_source(
        window, "communications", source_filter=lambda source_domain: not source_domain.startswith("worker_global:")
    )
    worker_payloads = admin_app.MainWindow._collect_config_form_payloads_by_source(
        window, "communications", source_filter=lambda source_domain: source_domain.startswith("worker_global:")
    )

    assert directory_payloads == {"providers": {"teams_backend": "graph_api"}}
    assert worker_payloads == {"worker_global:teams-ingest": {"poll_interval_seconds": 45.0}}


def test_save_config_domain_routes_mixed_scope_writes() -> None:
    writes: list[tuple[str, str, dict[str, object]]] = []

    class DummyResult:
        def __init__(self) -> None:
            self.ok = True
            self.errors = []

    class DummyText:
        def __init__(self) -> None:
            self.value = ""

        def setPlainText(self, value: str) -> None:
            self.value = value

    class DummyLabel:
        def __init__(self) -> None:
            self.value = ""

        def setText(self, value: str) -> None:
            self.value = value

    service = SimpleNamespace(
        directory_scoped_config_enabled=True,
        write_directory_domain_config=lambda directory_path, source_domain, payload: (
            writes.append(("directory", source_domain, payload)) or DummyResult()
        ),
        write_worker_global_config=lambda worker_id, payload: (
            writes.append(("worker", worker_id, payload)) or DummyResult()
        ),
    )
    window = SimpleNamespace(
        service=service,
        _selected_directory_path="root/team-a",
        _config_widgets={
            "communications": {
                "validation_view": DummyText(),
            }
        },
        _config_dirty_domains={"communications"},
        config_status_label=DummyLabel(),
        refresh_configuration=lambda: writes.append(("refresh", "", {})),
        _collect_config_form_payloads_by_source=lambda domain, source_filter=None: (
            admin_app.MainWindow._collect_config_form_payloads_by_source(window, domain, source_filter=source_filter)
        ),
        _collect_config_form_payload=lambda _domain: {
            "teams_backend": "graph_api",
            "poll_interval_seconds": 60.0,
        },
    )
    window._config_widgets["communications"]["field_domains"] = {
        "teams_backend": "providers",
        "poll_interval_seconds": "worker_global:teams-ingest",
    }

    admin_app.MainWindow.save_config_domain(
        window, "communications", source_filter=lambda source_domain: not source_domain.startswith("worker_global:")
    )
    admin_app.MainWindow.save_config_domain(
        window, "communications", source_filter=lambda source_domain: source_domain.startswith("worker_global:")
    )

    assert ("directory", "providers", {"teams_backend": "graph_api"}) in writes
    assert ("worker", "teams-ingest", {"poll_interval_seconds": 60.0}) in writes


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


def test_resume_vm_uses_same_context_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        admin_app.QInputDialog,
        "getMultiLineText",
        staticmethod(lambda *args, **kwargs: ("Resume prior work", True)),
    )

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, value: str) -> None:
            self.text = value

    window = SimpleNamespace(
        _validate_vm_model_configuration=lambda summary: None,
        vm_inline_validation_label=DummyLabel(),
        service=SimpleNamespace(resume_agent=object()),
        run_action=lambda label, fn, *args, **kwargs: captured.update(
            {"label": label, "fn": fn, "args": args, "kwargs": kwargs}
        ),
    )
    summary = SimpleNamespace(vm_name="vm-14084")

    admin_app.MainWindow.resume_vm(window, summary)

    assert captured["label"] == "Resume vm-14084"
    assert captured["args"] == ("vm-14084", "Resume prior work")
    assert captured["kwargs"]["resume_mode"] == "same_context"


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


def test_format_issue_details_includes_metadata_when_present() -> None:
    issue = SimpleNamespace(
        issue_number="14424",
        title="Planner test",
        state="In Progress",
        directory_path="root/example-industry/app",
        repo_source="/tmp/repo",
        yaml_path="runtime/issues/issue-14424.yaml",
        reason=None,
        description="",
        body="",
        metadata={"state": "In Progress", "steps": ["Analyze", "Implement"]},
    )

    formatted = admin_app._format_issue_details(issue)

    assert "Metadata:" in formatted
    assert '"steps": [' in formatted
    assert '"Analyze"' in formatted
    assert '"Implement"' in formatted


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
        kill_agent_button=DummyButton(),
        resume_agent_button=DummyButton(),
        destroy_vm_button=DummyButton(),
        selected_vm_summary=lambda: SimpleNamespace(
            vm_name="lab-vm",
            libvirt_state="running",
            issue_number=None,
            agent_state="unknown",
            agent_pid=None,
        ),
    )

    admin_app.MainWindow.update_vm_action_bar(window)

    assert window.vm_selection_hint.text == "Selected: lab-vm"
    assert window.destroy_vm_button.visible is True
    assert window.enable_agent_button.visible is False
    assert window.disable_agent_button.visible is False
    assert window.kill_agent_button.visible is False
    assert window.resume_agent_button.visible is False


def test_update_vm_action_bar_shows_kill_for_running_agent_pid() -> None:
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
        kill_agent_button=DummyButton(),
        resume_agent_button=DummyButton(),
        destroy_vm_button=DummyButton(),
        selected_vm_summary=lambda: SimpleNamespace(
            vm_name="vm-42",
            libvirt_state="running",
            issue_number="42",
            agent_state="running",
            agent_pid=4321,
        ),
    )

    admin_app.MainWindow.update_vm_action_bar(window)

    assert window.kill_agent_button.visible is True


def test_handle_kill_selected_vm_agent_uses_selected_pid() -> None:
    captured: dict[str, object] = {}
    window = SimpleNamespace(
        selected_vm_summary=lambda: SimpleNamespace(vm_name="vm-42", agent_pid=4321),
        service=SimpleNamespace(kill_agent=object()),
        run_action=lambda label, fn, *args: captured.update({"label": label, "fn": fn, "args": args}),
    )

    admin_app.MainWindow.handle_kill_selected_vm_agent(window)

    assert captured == {
        "label": "Kill agent vm-42 pid 4321",
        "fn": window.service.kill_agent,
        "args": ("vm-42", 4321),
    }


def test_update_session_action_bar_populates_session_model_controls() -> None:
    class DummyEdit:
        def __init__(self) -> None:
            self.enabled = None
            self.value = ""

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

        def setText(self, value: str) -> None:
            self.value = value

    class DummyButton:
        def __init__(self) -> None:
            self.enabled = None
            self.visible = False

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

        def show(self) -> None:
            self.visible = True

        def hide(self) -> None:
            self.visible = False

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, value: str) -> None:
            self.text = value

    window = SimpleNamespace(
        selected_session_summary=lambda: SimpleNamespace(
            session_id="session-1",
            model="gpt-5.3-codex",
            allowed_tools=[],
        ),
        _session_model_dirty=False,
        _session_model_bound_session_id=None,
        _session_model_server_value="",
        _session_tools_dirty=False,
        _session_tools_bound_session_id=None,
        _session_tools_server_values=set(),
        _sync_session_model_editor=lambda session_id, model_value: admin_app.MainWindow._sync_session_model_editor(
            window, session_id, model_value
        ),
        _sync_session_tools_editor=lambda session_id, allowed_tools: admin_app.MainWindow._sync_session_tools_editor(
            window, session_id, allowed_tools
        ),
        session_selection_hint=DummyLabel(),
        renew_session_button=DummyButton(),
        revoke_session_button=DummyButton(),
        delete_session_button=DummyButton(),
        session_model_edit=DummyEdit(),
        save_session_model_button=DummyButton(),
        session_model_status_label=DummyLabel(),
        save_session_tools_button=DummyButton(),
        session_tools_checkboxes={},
    )

    admin_app.MainWindow.update_session_action_bar(window)

    assert window.session_selection_hint.text == "Selected: session-1"
    assert window.session_model_edit.enabled is True
    assert window.session_model_edit.value == "gpt-5.3-codex"
    assert window.save_session_model_button.enabled is True
    assert window.session_model_status_label.text == ""


def test_update_session_action_bar_preserves_dirty_model_input_for_same_session() -> None:
    class DummyEdit:
        def __init__(self) -> None:
            self.enabled = None
            self.value = "gpt-5.4-mini-CANARY"

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

        def setText(self, value: str) -> None:
            self.value = value

    class DummyButton:
        def __init__(self) -> None:
            self.enabled = None
            self.visible = False

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

        def show(self) -> None:
            self.visible = True

        def hide(self) -> None:
            self.visible = False

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, value: str) -> None:
            self.text = value

    window = SimpleNamespace(
        selected_session_summary=lambda: SimpleNamespace(
            session_id="session-1",
            model="gpt-5.3-codex",
            allowed_tools=[],
        ),
        _session_model_dirty=True,
        _session_model_bound_session_id="session-1",
        _session_model_server_value="gpt-5.3-codex",
        _session_tools_dirty=False,
        _session_tools_bound_session_id="session-1",
        _session_tools_server_values=set(),
        _sync_session_model_editor=lambda session_id, model_value: admin_app.MainWindow._sync_session_model_editor(
            window, session_id, model_value
        ),
        _sync_session_tools_editor=lambda session_id, allowed_tools: admin_app.MainWindow._sync_session_tools_editor(
            window, session_id, allowed_tools
        ),
        session_selection_hint=DummyLabel(),
        renew_session_button=DummyButton(),
        revoke_session_button=DummyButton(),
        delete_session_button=DummyButton(),
        session_model_edit=DummyEdit(),
        save_session_model_button=DummyButton(),
        session_model_status_label=DummyLabel(),
        save_session_tools_button=DummyButton(),
        session_tools_checkboxes={},
    )

    admin_app.MainWindow.update_session_action_bar(window)

    assert window.session_model_edit.value == "gpt-5.4-mini-CANARY"
    assert window._session_model_server_value == "gpt-5.3-codex"
    assert window._session_model_dirty is True


def test_update_session_action_bar_replaces_model_input_when_session_changes() -> None:
    class DummyEdit:
        def __init__(self) -> None:
            self.enabled = None
            self.value = "unsaved-value"

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

        def setText(self, value: str) -> None:
            self.value = value

    class DummyButton:
        def __init__(self) -> None:
            self.enabled = None
            self.visible = False

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

        def show(self) -> None:
            self.visible = True

        def hide(self) -> None:
            self.visible = False

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, value: str) -> None:
            self.text = value

    window = SimpleNamespace(
        selected_session_summary=lambda: SimpleNamespace(
            session_id="session-2",
            model="gpt-5.4-mini",
            allowed_tools=[],
        ),
        _session_model_dirty=True,
        _session_model_bound_session_id="session-1",
        _session_model_server_value="gpt-5.3-codex",
        _session_tools_dirty=False,
        _session_tools_bound_session_id="session-1",
        _session_tools_server_values=set(),
        _sync_session_model_editor=lambda session_id, model_value: admin_app.MainWindow._sync_session_model_editor(
            window, session_id, model_value
        ),
        _sync_session_tools_editor=lambda session_id, allowed_tools: admin_app.MainWindow._sync_session_tools_editor(
            window, session_id, allowed_tools
        ),
        session_selection_hint=DummyLabel(),
        renew_session_button=DummyButton(),
        revoke_session_button=DummyButton(),
        delete_session_button=DummyButton(),
        session_model_edit=DummyEdit(),
        save_session_model_button=DummyButton(),
        session_model_status_label=DummyLabel(),
        save_session_tools_button=DummyButton(),
        session_tools_checkboxes={},
    )

    admin_app.MainWindow.update_session_action_bar(window)

    assert window.session_model_edit.value == "gpt-5.4-mini"
    assert window._session_model_bound_session_id == "session-2"
    assert window._session_model_dirty is False


def test_update_session_action_bar_preserves_dirty_tool_checkboxes_for_same_session() -> None:
    class DummyEdit:
        def __init__(self) -> None:
            self.enabled = None
            self.value = ""

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

        def setText(self, value: str) -> None:
            self.value = value

    class DummyButton:
        def __init__(self) -> None:
            self.enabled = None
            self.visible = False

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

        def show(self) -> None:
            self.visible = True

        def hide(self) -> None:
            self.visible = False

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, value: str) -> None:
            self.text = value

    class DummyCheckbox:
        def __init__(self, checked: bool) -> None:
            self.enabled = None
            self.checked = checked

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

        def setChecked(self, value: bool) -> None:
            self.checked = value

        def isChecked(self) -> bool:
            return self.checked

    checkbox = DummyCheckbox(True)
    window = SimpleNamespace(
        selected_session_summary=lambda: SimpleNamespace(
            session_id="session-1",
            model="gpt-5.3-codex",
            allowed_tools=[],
        ),
        _session_model_dirty=False,
        _session_model_bound_session_id="session-1",
        _session_model_server_value="gpt-5.3-codex",
        _session_tools_dirty=True,
        _session_tools_bound_session_id="session-1",
        _session_tools_server_values=set(),
        _sync_session_model_editor=lambda session_id, model_value: admin_app.MainWindow._sync_session_model_editor(
            window, session_id, model_value
        ),
        _sync_session_tools_editor=lambda session_id, allowed_tools: admin_app.MainWindow._sync_session_tools_editor(
            window, session_id, allowed_tools
        ),
        session_selection_hint=DummyLabel(),
        renew_session_button=DummyButton(),
        revoke_session_button=DummyButton(),
        delete_session_button=DummyButton(),
        session_model_edit=DummyEdit(),
        save_session_model_button=DummyButton(),
        session_model_status_label=DummyLabel(),
        save_session_tools_button=DummyButton(),
        session_tools_checkboxes={admin_app.ToolName.APPROVAL_CHECK: checkbox},
    )

    admin_app.MainWindow.update_session_action_bar(window)

    assert checkbox.checked is True
    assert checkbox.enabled is True
    assert window._session_tools_server_values == set()
    assert window._session_tools_dirty is True


def test_update_session_action_bar_replaces_tool_checkboxes_when_session_changes() -> None:
    class DummyEdit:
        def __init__(self) -> None:
            self.enabled = None
            self.value = ""

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

        def setText(self, value: str) -> None:
            self.value = value

    class DummyButton:
        def __init__(self) -> None:
            self.enabled = None
            self.visible = False

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

        def show(self) -> None:
            self.visible = True

        def hide(self) -> None:
            self.visible = False

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, value: str) -> None:
            self.text = value

    class DummyCheckbox:
        def __init__(self, checked: bool) -> None:
            self.enabled = None
            self.checked = checked

        def setEnabled(self, value: bool) -> None:
            self.enabled = value

        def setChecked(self, value: bool) -> None:
            self.checked = value

        def isChecked(self) -> bool:
            return self.checked

    checkbox = DummyCheckbox(False)
    window = SimpleNamespace(
        selected_session_summary=lambda: SimpleNamespace(
            session_id="session-2",
            model="gpt-5.3-codex",
            allowed_tools=[admin_app.ToolName.APPROVAL_CHECK.value],
        ),
        _session_model_dirty=False,
        _session_model_bound_session_id="session-2",
        _session_model_server_value="gpt-5.3-codex",
        _session_tools_dirty=True,
        _session_tools_bound_session_id="session-1",
        _session_tools_server_values=set(),
        _sync_session_model_editor=lambda session_id, model_value: admin_app.MainWindow._sync_session_model_editor(
            window, session_id, model_value
        ),
        _sync_session_tools_editor=lambda session_id, allowed_tools: admin_app.MainWindow._sync_session_tools_editor(
            window, session_id, allowed_tools
        ),
        session_selection_hint=DummyLabel(),
        renew_session_button=DummyButton(),
        revoke_session_button=DummyButton(),
        delete_session_button=DummyButton(),
        session_model_edit=DummyEdit(),
        save_session_model_button=DummyButton(),
        session_model_status_label=DummyLabel(),
        save_session_tools_button=DummyButton(),
        session_tools_checkboxes={admin_app.ToolName.APPROVAL_CHECK: checkbox},
    )

    admin_app.MainWindow.update_session_action_bar(window)

    assert checkbox.checked is True
    assert window._session_tools_bound_session_id == "session-2"
    assert window._session_tools_server_values == {admin_app.ToolName.APPROVAL_CHECK.value}
    assert window._session_tools_dirty is False


def test_handle_save_selected_session_model_requires_non_empty_input() -> None:
    class DummyEdit:
        def text(self) -> str:
            return "   "

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, value: str) -> None:
            self.text = value

    window = SimpleNamespace(
        selected_session_summary=lambda: SimpleNamespace(session_id="session-1"),
        session_model_edit=DummyEdit(),
        session_model_status_label=DummyLabel(),
        run_action=lambda *args, **kwargs: None,
        service=SimpleNamespace(set_session_model=object()),
    )

    admin_app.MainWindow.handle_save_selected_session_model(window)

    assert window.session_model_status_label.text == "Model is required."


def test_handle_save_selected_session_tools_success_callback_accepts_action_result() -> None:
    class DummyCheckbox:
        def __init__(self, checked: bool) -> None:
            self.checked = checked

        def isChecked(self) -> bool:
            return self.checked

    captured: dict[str, object] = {}

    def run_action(*args, **kwargs) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    saved: list[tuple[str, list[str]]] = []
    window = SimpleNamespace(
        selected_session_summary=lambda: SimpleNamespace(session_id="session-1"),
        session_tools_checkboxes={
            admin_app.ToolName.APPROVAL_CHECK: DummyCheckbox(True),
            admin_app.ToolName.APPROVAL_SUBMIT: DummyCheckbox(False),
        },
        service=SimpleNamespace(set_session_allowed_tools=object()),
        run_action=run_action,
        _handle_session_tools_save_success=lambda session_id, selected_tools: saved.append(
            (session_id, selected_tools)
        ),
    )

    admin_app.MainWindow.handle_save_selected_session_tools(window)
    captured["kwargs"]["on_success"](SimpleNamespace(success=True, message="updated"))

    assert captured["args"][0] == "Update allowed tools for session-1"
    assert captured["args"][2:] == ("session-1", [admin_app.ToolName.APPROVAL_CHECK.value])
    assert saved == [("session-1", [admin_app.ToolName.APPROVAL_CHECK.value])]


def test_handle_save_selected_session_model_success_callback_accepts_action_result() -> None:
    class DummyEdit:
        def text(self) -> str:
            return " gpt-5.4 "

    class DummyLabel:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, value: str) -> None:
            self.text = value

    captured: dict[str, object] = {}

    def run_action(*args, **kwargs) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    saved: list[tuple[str, str]] = []
    window = SimpleNamespace(
        selected_session_summary=lambda: SimpleNamespace(session_id="session-1"),
        session_model_edit=DummyEdit(),
        session_model_status_label=DummyLabel(),
        service=SimpleNamespace(set_session_model=object()),
        run_action=run_action,
        _handle_session_model_save_success=lambda session_id, model: saved.append((session_id, model)),
    )

    admin_app.MainWindow.handle_save_selected_session_model(window)
    captured["kwargs"]["on_success"](SimpleNamespace(success=True, message="updated"))

    assert window.session_model_status_label.text == ""
    assert captured["args"][0] == "Update model for session-1"
    assert captured["args"][2:] == ("session-1", "gpt-5.4")
    assert saved == [("session-1", "gpt-5.4")]


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
