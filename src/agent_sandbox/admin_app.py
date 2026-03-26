from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .admin_service import AdminService

try:
    from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal
    from PySide6.QtWidgets import (
        QApplication,
        QButtonGroup,
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QRadioButton,
        QSplitter,
        QStatusBar,
        QAbstractItemView,
        QStyle,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextEdit,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover - exercised only when GUI extra is missing.
    QApplication = None
    QObject = object
    QRunnable = object
    QThreadPool = object
    QTimer = object
    Qt = object
    Signal = lambda *args, **kwargs: None
    QButtonGroup = object
    QCheckBox = QDialog = QDialogButtonBox = QFileDialog = QFormLayout = QHBoxLayout = object
    QHeaderView = QInputDialog = QLabel = QLineEdit = QMainWindow = QMessageBox = object
    QAbstractItemView = object
    QStyle = object
    QPushButton = QPlainTextEdit = QRadioButton = QSplitter = QStatusBar = QTableWidget = QTableWidgetItem = object
    QToolButton = object
    QTabWidget = QTextEdit = QVBoxLayout = QWidget = object


class _WorkerSignals(QObject):  # type: ignore[misc]
    finished = Signal(object)
    failed = Signal(str)


class _ActionWorker(QRunnable):  # type: ignore[misc]
    def __init__(self, fn, *args, **kwargs) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # pragma: no cover - GUI-only path
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(result)


class IssueDialog(QDialog):  # type: ignore[misc]
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Local Issue")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.manual_radio = QRadioButton("Create local issue from form")
        self.import_radio = QRadioButton("Import existing YAML")
        self.manual_radio.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.manual_radio)
        self.mode_group.addButton(self.import_radio)
        self.title_edit = QLineEdit()
        self.repo_source_edit = QLineEdit()
        self.yaml_path_edit = QLineEdit()
        self.body_edit = QPlainTextEdit()
        self.start_vm_checkbox = QCheckBox("Create and start VM immediately")

        yaml_row = QWidget()
        yaml_layout = QHBoxLayout(yaml_row)
        yaml_layout.setContentsMargins(0, 0, 0, 0)
        yaml_layout.addWidget(self.yaml_path_edit)
        browse_button = QPushButton("Browse")
        _set_standard_icon(browse_button, "SP_DialogOpenButton")
        browse_button.clicked.connect(self._browse_yaml)
        yaml_layout.addWidget(browse_button)

        layout.addWidget(self.manual_radio)
        layout.addWidget(self.import_radio)
        form.addRow("Title", self.title_edit)
        form.addRow("Repo source", self.repo_source_edit)
        form.addRow("Import YAML", yaml_row)
        form.addRow("Body", self.body_edit)
        layout.addLayout(form)
        layout.addWidget(self.start_vm_checkbox)

        self.manual_radio.toggled.connect(self._update_mode)
        self.import_radio.toggled.connect(self._update_mode)
        self._update_mode()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_yaml(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Select issue YAML", "", "YAML Files (*.yaml *.yml)")
        if selected:
            self.yaml_path_edit.setText(selected)
            self.import_radio.setChecked(True)

    def _update_mode(self) -> None:
        manual_mode = self.manual_radio.isChecked()
        self.title_edit.setEnabled(manual_mode)
        self.body_edit.setEnabled(manual_mode)
        self.yaml_path_edit.setEnabled(not manual_mode)

    def payload(self) -> dict[str, object]:
        manual_mode = self.manual_radio.isChecked()
        yaml_path = self.yaml_path_edit.text().strip() or None
        return {
            "title": self.title_edit.text().strip() if manual_mode else "",
            "body": self.body_edit.toPlainText().strip() if manual_mode else "",
            "repo_source": self.repo_source_edit.text().strip() or None,
            "yaml_path": None if manual_mode else yaml_path,
            "start_vm": self.start_vm_checkbox.isChecked(),
        }


class CreateVmDialog(QDialog):  # type: ignore[misc]
    def __init__(self, issue_number: str, repo_source: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Create VM for Issue {issue_number}")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.repo_source_edit = QLineEdit(repo_source or "")
        self.python_checkbox = QCheckBox("Bootstrap Python")
        self.dotnet_checkbox = QCheckBox("Bootstrap .NET")
        self.nuget_checkbox = QCheckBox("Bootstrap NuGet")
        self.nuget_target_edit = QLineEdit()
        self.nuget_target_edit.setPlaceholderText("/path/to/project-or-solution.slnx")
        self.nuget_target_row = QWidget()
        nuget_target_layout = QHBoxLayout(self.nuget_target_row)
        nuget_target_layout.setContentsMargins(0, 0, 0, 0)
        nuget_target_layout.addWidget(self.nuget_target_edit)
        self.playwright_checkbox = QCheckBox("Bootstrap Playwright")
        self.mssql_checkbox = QCheckBox("Bootstrap MSSQL")
        self.mssql_available = bool(os.environ.get("MSSQL_SA_PASSWORD", "").strip())
        if not self.mssql_available:
            self.mssql_checkbox.setEnabled(False)
            self.mssql_checkbox.setToolTip("Set MSSQL_SA_PASSWORD in the host environment to enable MSSQL bootstrap.")
        self.mssql_init_root_edit = QLineEdit()
        self.mssql_init_root_edit.setPlaceholderText("/path/to/mssql-init")
        self.mssql_init_root_row = QWidget()
        mssql_init_root_layout = QHBoxLayout(self.mssql_init_root_row)
        mssql_init_root_layout.setContentsMargins(0, 0, 0, 0)
        mssql_init_root_layout.addWidget(self.mssql_init_root_edit)
        self.mssql_hint_label = QLabel(
            "MSSQL bootstrap is disabled because MSSQL_SA_PASSWORD is not set in the host environment."
        )
        self.mssql_hint_label.setWordWrap(True)
        self.mssql_hint_label.setVisible(not self.mssql_available)
        self.start_agent_checkbox = QCheckBox("Start agent after provisioning")
        self.start_agent_checkbox.setChecked(True)

        form.addRow("Repo source", self.repo_source_edit)
        form.addRow("", self.python_checkbox)
        form.addRow("", self.dotnet_checkbox)
        form.addRow("", self.nuget_checkbox)
        form.addRow("NuGet target", self.nuget_target_row)
        form.addRow("", self.playwright_checkbox)
        form.addRow("", self.mssql_checkbox)
        form.addRow("", self.mssql_hint_label)
        form.addRow("MSSQL init root", self.mssql_init_root_row)
        layout.addLayout(form)
        layout.addWidget(self.start_agent_checkbox)

        self.nuget_checkbox.toggled.connect(self._update_state)
        self.mssql_checkbox.toggled.connect(self._update_state)
        self._update_state()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_state(self) -> None:
        self.nuget_target_row.setVisible(self.nuget_checkbox.isChecked())
        self.mssql_init_root_row.setVisible(self.mssql_checkbox.isChecked())

    def payload(self) -> dict[str, object]:
        nuget = self.nuget_target_edit.text().strip() or None
        mssql_init_root = self.mssql_init_root_edit.text().strip() or None
        return {
            "repo_source": self.repo_source_edit.text().strip() or None,
            "python": self.python_checkbox.isChecked(),
            "dotnet": self.dotnet_checkbox.isChecked(),
            "nuget": nuget if self.nuget_checkbox.isChecked() else None,
            "playwright": self.playwright_checkbox.isChecked(),
            "mssql": self.mssql_checkbox.isChecked(),
            "mssql_init_root": mssql_init_root if self.mssql_checkbox.isChecked() else None,
            "start_agent": self.start_agent_checkbox.isChecked(),
        }


class CollapsibleSection(QWidget):  # type: ignore[misc]
    def __init__(self, title: str, content_widget: QWidget, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.toggle_button = QToolButton()
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.ArrowType.DownArrow)
        self.toggle_button.toggled.connect(self._on_toggled)
        layout.addWidget(self.toggle_button)

        self.content_widget = content_widget
        layout.addWidget(self.content_widget)

    def _on_toggled(self, expanded: bool) -> None:
        self.content_widget.setVisible(expanded)
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )

    def is_expanded(self) -> bool:
        return self.toggle_button.isChecked()

    def collapsed_height(self) -> int:
        margins = self.layout().contentsMargins()
        spacing = self.layout().spacing()
        return self.toggle_button.sizeHint().height() + margins.top() + margins.bottom() + max(spacing, 0)


def _set_standard_icon(widget, icon_name: str) -> None:
    style = widget.style()
    icon_enum = getattr(QStyle.StandardPixmap, icon_name, None)
    if style is None or icon_enum is None:
        return
    widget.setIcon(style.standardIcon(icon_enum))


def _set_text_preserving_scroll(text_edit, text: str) -> None:
    scroll_bar = text_edit.verticalScrollBar()
    if scroll_bar is None:
        text_edit.setPlainText(text)
        return

    previous_value = scroll_bar.value()
    previous_maximum = scroll_bar.maximum()
    was_at_bottom = previous_maximum <= 0 or previous_value >= previous_maximum

    text_edit.setPlainText(text)

    updated_scroll_bar = text_edit.verticalScrollBar()
    if updated_scroll_bar is None:
        return
    if was_at_bottom:
        updated_scroll_bar.setValue(updated_scroll_bar.maximum())
        return
    updated_scroll_bar.setValue(min(previous_value, updated_scroll_bar.maximum()))


def _truncate_text(value: str, *, max_length: int = 36) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3]}..."


def _format_session_details(summary) -> str:
    payload = summary.model_dump(mode="json")
    payload["allowed_tools"] = [str(tool) for tool in summary.allowed_tools]
    return json.dumps(payload, indent=2, sort_keys=True)


class MainWindow(QMainWindow):  # type: ignore[misc]
    def __init__(self, service: AdminService) -> None:
        super().__init__()
        self.service = service
        self.thread_pool = QThreadPool()
        self._active_workers: set[_ActionWorker] = set()
        self._vm_summaries = []
        self._issue_records = []
        self._active_session_summaries = []
        self._expired_session_summaries = []
        self._worker_summaries = []
        self.setWindowTitle("Agent Sandbox Admin")
        self.resize(1360, 820)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.vm_table = QTableWidget(0, 8)
        self.vm_table.setHorizontalHeaderLabels(
            ["VM", "Issue", "Sessions", "Power", "IP", "Agent", "Repos", "Updated"]
        )
        self.vm_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.vm_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.vm_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.vm_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.vm_table.itemSelectionChanged.connect(self._on_vm_selection_changed)

        self.issue_table = QTableWidget(0, 4)
        self.issue_table.setHorizontalHeaderLabels(["Issue", "Title", "Repo source", "YAML"])
        self.issue_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.issue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.issue_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.issue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.issue_table.itemSelectionChanged.connect(self._on_issue_selection_changed)

        self.worker_table = QTableWidget(0, 8)
        self.worker_table.setHorizontalHeaderLabels(
            ["Worker", "Unit", "State", "Substate", "Result", "PID", "Enabled", "Detail"]
        )
        self.worker_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.worker_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.worker_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.worker_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.worker_table.itemSelectionChanged.connect(self._on_worker_selection_changed)

        self.active_session_table = QTableWidget(0, 4)
        self.active_session_table.setHorizontalHeaderLabels(["Name", "VM", "Issue", "Expires"])
        self.active_session_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.active_session_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.active_session_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.active_session_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.active_session_table.itemSelectionChanged.connect(self._on_active_session_selection_changed)

        self.expired_session_table = QTableWidget(0, 4)
        self.expired_session_table.setHorizontalHeaderLabels(["Name", "VM", "Issue", "Expires"])
        self.expired_session_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.expired_session_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.expired_session_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.expired_session_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.expired_session_table.itemSelectionChanged.connect(self._on_expired_session_selection_changed)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.agent_log_view = QTextEdit()
        self.agent_log_view.setReadOnly(True)
        self.agent_log_view.setPlaceholderText("Select an issue VM to see its agent log.")
        self.session_detail_view = QTextEdit()
        self.session_detail_view.setReadOnly(True)
        self.session_detail_view.setPlaceholderText("Select a session to see its full details.")
        self.worker_log_view = QTextEdit()
        self.worker_log_view.setReadOnly(True)
        self.worker_log_view.setPlaceholderText("Select a background worker to see its logs.")

        vm_page = QWidget()
        vm_layout = QVBoxLayout(vm_page)
        vm_toolbar = QHBoxLayout()
        refresh_vms_button = QPushButton("Refresh")
        _set_standard_icon(refresh_vms_button, "SP_BrowserReload")
        refresh_vms_button.clicked.connect(self.refresh_vms)
        vm_toolbar.addWidget(refresh_vms_button)
        vm_toolbar.addStretch(1)
        self.vm_selection_hint = QLabel("No VM selected")
        vm_toolbar.addWidget(self.vm_selection_hint)
        self.vm_action_bar = QHBoxLayout()
        self.vm_action_bar.setSpacing(6)
        self.start_vm_button = QPushButton("Start")
        _set_standard_icon(self.start_vm_button, "SP_MediaPlay")
        self.start_vm_button.clicked.connect(self.handle_start_selected_vm)
        self.shutdown_vm_button = QPushButton("Shutdown")
        _set_standard_icon(self.shutdown_vm_button, "SP_MediaStop")
        self.shutdown_vm_button.clicked.connect(self.handle_shutdown_selected_vm)
        self.enable_agent_button = QPushButton("Enable")
        _set_standard_icon(self.enable_agent_button, "SP_DialogApplyButton")
        self.enable_agent_button.clicked.connect(self.handle_enable_selected_vm)
        self.disable_agent_button = QPushButton("Disable")
        _set_standard_icon(self.disable_agent_button, "SP_DialogCancelButton")
        self.disable_agent_button.clicked.connect(self.handle_disable_selected_vm)
        self.resume_agent_button = QPushButton("Resume")
        _set_standard_icon(self.resume_agent_button, "SP_BrowserReload")
        self.resume_agent_button.clicked.connect(self.handle_resume_selected_vm)
        self.destroy_vm_button = QPushButton("Destroy")
        _set_standard_icon(self.destroy_vm_button, "SP_TrashIcon")
        self.destroy_vm_button.clicked.connect(self.handle_destroy_selected_vm)
        for button in (
            self.start_vm_button,
            self.shutdown_vm_button,
            self.enable_agent_button,
            self.disable_agent_button,
            self.resume_agent_button,
            self.destroy_vm_button,
        ):
            self.vm_action_bar.addWidget(button)
        vm_toolbar.addLayout(self.vm_action_bar)
        vm_layout.addLayout(vm_toolbar)
        vm_splitter = QSplitter(Qt.Orientation.Vertical)
        self.vm_splitter = vm_splitter
        vm_splitter.addWidget(self.vm_table)
        log_panel = QWidget()
        self.vm_log_panel = log_panel
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        self.operator_log_section = CollapsibleSection("Operator log", self.log_view)
        self.agent_log_section = CollapsibleSection("Selected agent log", self.agent_log_view)
        log_layout.addWidget(self.operator_log_section)
        log_layout.addWidget(self.agent_log_section)
        vm_splitter.addWidget(log_panel)
        vm_splitter.setStretchFactor(0, 1)
        vm_splitter.setStretchFactor(1, 1)
        vm_layout.addWidget(vm_splitter)
        self.operator_log_section.toggle_button.toggled.connect(self._update_vm_splitter_layout)
        self.agent_log_section.toggle_button.toggled.connect(self._update_vm_splitter_layout)

        issue_page = QWidget()
        issue_layout = QVBoxLayout(issue_page)
        issue_toolbar = QHBoxLayout()
        refresh_issues_button = QPushButton("Refresh")
        _set_standard_icon(refresh_issues_button, "SP_BrowserReload")
        refresh_issues_button.clicked.connect(self.refresh_issues)
        new_issue_button = QPushButton("New Issue")
        _set_standard_icon(new_issue_button, "SP_FileIcon")
        new_issue_button.clicked.connect(self.show_new_issue_dialog)
        issue_toolbar.addWidget(new_issue_button)
        issue_toolbar.addWidget(refresh_issues_button)
        issue_toolbar.addStretch(1)
        self.issue_selection_hint = QLabel("No issue selected")
        issue_toolbar.addWidget(self.issue_selection_hint)
        self.issue_action_bar = QHBoxLayout()
        self.issue_action_bar.setSpacing(6)
        self.create_issue_vm_button = QPushButton("Create VM")
        _set_standard_icon(self.create_issue_vm_button, "SP_ComputerIcon")
        self.create_issue_vm_button.clicked.connect(self.handle_create_vm_selected_issue)
        self.delete_issue_button = QPushButton("Delete")
        _set_standard_icon(self.delete_issue_button, "SP_TrashIcon")
        self.delete_issue_button.clicked.connect(self.handle_delete_selected_issue)
        self.issue_action_bar.addWidget(self.create_issue_vm_button)
        self.issue_action_bar.addWidget(self.delete_issue_button)
        issue_toolbar.addLayout(self.issue_action_bar)
        issue_layout.addLayout(issue_toolbar)
        issue_layout.addWidget(self.issue_table)

        sessions_page = QWidget()
        sessions_layout = QVBoxLayout(sessions_page)
        sessions_toolbar = QHBoxLayout()
        refresh_sessions_button = QPushButton("Refresh")
        _set_standard_icon(refresh_sessions_button, "SP_BrowserReload")
        refresh_sessions_button.clicked.connect(self.refresh_sessions)
        sessions_toolbar.addWidget(refresh_sessions_button)
        sessions_toolbar.addStretch(1)
        self.session_selection_hint = QLabel("No session selected")
        sessions_toolbar.addWidget(self.session_selection_hint)
        self.session_action_bar = QHBoxLayout()
        self.session_action_bar.setSpacing(6)
        self.renew_session_button = QPushButton("Renew")
        _set_standard_icon(self.renew_session_button, "SP_BrowserReload")
        self.renew_session_button.clicked.connect(self.handle_renew_selected_session)
        self.revoke_session_button = QPushButton("Revoke")
        _set_standard_icon(self.revoke_session_button, "SP_DialogCancelButton")
        self.revoke_session_button.clicked.connect(self.handle_revoke_selected_session)
        self.delete_session_button = QPushButton("Delete")
        _set_standard_icon(self.delete_session_button, "SP_TrashIcon")
        self.delete_session_button.clicked.connect(self.handle_delete_selected_session)
        for button in (
            self.renew_session_button,
            self.revoke_session_button,
            self.delete_session_button,
        ):
            self.session_action_bar.addWidget(button)
        sessions_toolbar.addLayout(self.session_action_bar)
        sessions_layout.addLayout(sessions_toolbar)
        self.session_tabs = QTabWidget()
        active_sessions_page = QWidget()
        active_sessions_layout = QVBoxLayout(active_sessions_page)
        active_sessions_layout.setContentsMargins(0, 0, 0, 0)
        active_sessions_layout.addWidget(self.active_session_table)
        expired_sessions_page = QWidget()
        expired_sessions_layout = QVBoxLayout(expired_sessions_page)
        expired_sessions_layout.setContentsMargins(0, 0, 0, 0)
        expired_sessions_layout.addWidget(self.expired_session_table)
        self.session_tabs.addTab(active_sessions_page, "Active")
        self.session_tabs.addTab(expired_sessions_page, "Expired")
        self.session_tabs.currentChanged.connect(self._on_session_tab_changed)
        session_splitter = QSplitter(Qt.Orientation.Vertical)
        session_splitter.addWidget(self.session_tabs)
        session_splitter.addWidget(self.session_detail_view)
        session_splitter.setStretchFactor(0, 1)
        session_splitter.setStretchFactor(1, 1)
        sessions_layout.addWidget(session_splitter)

        worker_page = QWidget()
        worker_layout = QVBoxLayout(worker_page)
        worker_toolbar = QHBoxLayout()
        refresh_workers_button = QPushButton("Refresh")
        _set_standard_icon(refresh_workers_button, "SP_BrowserReload")
        refresh_workers_button.clicked.connect(self.refresh_workers)
        worker_toolbar.addWidget(refresh_workers_button)
        worker_toolbar.addStretch(1)
        self.worker_selection_hint = QLabel("No worker selected")
        worker_toolbar.addWidget(self.worker_selection_hint)
        self.worker_action_bar = QHBoxLayout()
        self.worker_action_bar.setSpacing(6)
        self.start_worker_button = QPushButton("Start")
        _set_standard_icon(self.start_worker_button, "SP_MediaPlay")
        self.start_worker_button.clicked.connect(self.handle_start_selected_worker)
        self.stop_worker_button = QPushButton("Stop")
        _set_standard_icon(self.stop_worker_button, "SP_MediaStop")
        self.stop_worker_button.clicked.connect(self.handle_stop_selected_worker)
        self.worker_action_bar.addWidget(self.start_worker_button)
        self.worker_action_bar.addWidget(self.stop_worker_button)
        worker_toolbar.addLayout(self.worker_action_bar)
        worker_layout.addLayout(worker_toolbar)
        worker_splitter = QSplitter(Qt.Orientation.Vertical)
        worker_splitter.addWidget(self.worker_table)
        worker_splitter.addWidget(self.worker_log_view)
        worker_splitter.setStretchFactor(0, 1)
        worker_splitter.setStretchFactor(1, 1)
        worker_layout.addWidget(worker_splitter)

        self.tabs.addTab(vm_page, "VMs")
        self.tabs.addTab(issue_page, "Issues")
        self.tabs.addTab(sessions_page, "Sessions")
        self.tabs.addTab(worker_page, "Workers")

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(5000)
        self.refresh_timer.timeout.connect(self.refresh_vms)
        self.refresh_timer.timeout.connect(self.refresh_issues)
        self.refresh_timer.timeout.connect(self.refresh_sessions)
        self.refresh_timer.timeout.connect(self.refresh_workers)
        self.refresh_timer.start()

        self.refresh_vms()
        self.refresh_issues()
        self.refresh_sessions()
        self.refresh_workers()
        self.update_vm_action_bar()
        self.update_issue_action_bar()
        self.update_session_action_bar()
        self.update_worker_action_bar()
        self._update_vm_splitter_layout()

    def append_log(self, message: str) -> None:
        self.log_view.append(message)
        self.status_bar.showMessage(message, 8000)

    def run_action(self, label: str, fn, *args, on_success=None, **kwargs) -> None:
        self.append_log(f"{label}...")
        worker = _ActionWorker(fn, *args, **kwargs)
        self._active_workers.add(worker)
        worker.signals.finished.connect(
            lambda result, worker=worker: self._handle_action_success(label, result, on_success, worker)
        )
        worker.signals.failed.connect(
            lambda error, worker=worker: self._handle_action_failure(label, error, worker)
        )
        self.thread_pool.start(worker)

    def _discard_worker(self, worker: _ActionWorker) -> None:
        self._active_workers.discard(worker)

    def _handle_action_success(self, label: str, result, on_success, worker: _ActionWorker) -> None:
        self._discard_worker(worker)
        message = getattr(result, "message", str(result))
        self.append_log(f"{label}: {message}")
        if on_success is not None:
            on_success(result)
        self.refresh_vms()
        self.refresh_issues()
        self.refresh_workers()

    def _handle_action_failure(self, label: str, error: str, worker: _ActionWorker) -> None:
        self._discard_worker(worker)
        self.append_log(f"{label} failed: {error}")
        QMessageBox.critical(self, "Action failed", error)

    def refresh_vms(self) -> None:
        previously_selected_vm = None
        current_summary = self.selected_vm_summary()
        if current_summary is not None:
            previously_selected_vm = current_summary.vm_name
        try:
            summaries = self.service.list_vms(include_inactive=True, vm_user="agent")
        except Exception as exc:
            self.append_log(f"Refresh VMs failed: {exc}")
            return
        self._vm_summaries = summaries
        self.vm_table.clearSelection()
        self.vm_table.setRowCount(len(summaries))
        for row, summary in enumerate(summaries):
            self.vm_table.setItem(row, 0, QTableWidgetItem(summary.vm_name))
            self.vm_table.setItem(row, 1, QTableWidgetItem(summary.issue_number or ""))
            self.vm_table.setItem(row, 2, QTableWidgetItem(", ".join(summary.session_ids)))
            self.vm_table.setItem(row, 3, QTableWidgetItem(summary.libvirt_state))
            self.vm_table.setItem(row, 4, QTableWidgetItem(summary.ip_address or ""))
            self.vm_table.setItem(row, 5, QTableWidgetItem(summary.agent_state))
            self.vm_table.setItem(row, 6, QTableWidgetItem(", ".join(summary.agent_repo_names)))
            updated = summary.last_state_update_at.isoformat() if summary.last_state_update_at else ""
            self.vm_table.setItem(row, 7, QTableWidgetItem(updated))
        if previously_selected_vm:
            restored = False
            for row, summary in enumerate(summaries):
                if summary.vm_name == previously_selected_vm:
                    self.vm_table.selectRow(row)
                    restored = True
                    break
            if not restored:
                self.vm_table.setCurrentCell(-1, -1)
        self.update_vm_action_bar()
        self.refresh_selected_agent_log()

    def selected_vm_summary(self):
        row = self.vm_table.currentRow()
        if row < 0 or row >= len(self._vm_summaries):
            return None
        return self._vm_summaries[row]

    def update_vm_action_bar(self) -> None:
        summary = self.selected_vm_summary()
        if summary is None:
            self.vm_selection_hint.setText("No VM selected")
            self.vm_selection_hint.show()
            for button in (
                self.start_vm_button,
                self.shutdown_vm_button,
                self.enable_agent_button,
                self.disable_agent_button,
                self.resume_agent_button,
                self.destroy_vm_button,
            ):
                button.hide()
            return

        self.vm_selection_hint.setText(f"Selected: {summary.vm_name}")
        self.vm_selection_hint.show()
        self.start_vm_button.setVisible(summary.libvirt_state != "running")
        self.shutdown_vm_button.setVisible(summary.libvirt_state == "running")
        self.enable_agent_button.setVisible(bool(summary.issue_number) and summary.agent_state == "disabled")
        self.disable_agent_button.setVisible(bool(summary.issue_number) and summary.agent_state != "disabled")
        self.resume_agent_button.setVisible(bool(summary.issue_number) and summary.agent_state != "disabled")
        self.destroy_vm_button.setVisible(bool(summary.issue_number))

    def _on_vm_selection_changed(self) -> None:
        self.update_vm_action_bar()
        self.refresh_selected_agent_log()

    def _update_vm_splitter_layout(self) -> None:
        any_logs_expanded = self.operator_log_section.is_expanded() or self.agent_log_section.is_expanded()
        if any_logs_expanded:
            self.vm_splitter.setSizes([1, 1])
        else:
            collapsed_height = (
                self.operator_log_section.collapsed_height()
                + self.agent_log_section.collapsed_height()
                + self.vm_log_panel.layout().contentsMargins().top()
                + self.vm_log_panel.layout().contentsMargins().bottom()
                + self.vm_log_panel.layout().spacing()
            )
            self.vm_log_panel.setMinimumHeight(collapsed_height)
            self.vm_log_panel.setMaximumHeight(collapsed_height)
            self.vm_splitter.setSizes([1000, collapsed_height])
            return
        self.vm_log_panel.setMinimumHeight(0)
        self.vm_log_panel.setMaximumHeight(16777215)

    def refresh_selected_agent_log(self) -> None:
        summary = self.selected_vm_summary()
        if summary is None:
            _set_text_preserving_scroll(self.agent_log_view, "No VM selected.")
            return
        if not summary.issue_number:
            _set_text_preserving_scroll(self.agent_log_view, "Agent log unavailable for VMs without an issue number.")
            return
        text = self.service.get_agent_log_snapshot(summary.issue_number, lines=50)
        _set_text_preserving_scroll(self.agent_log_view, text)

    def handle_start_selected_vm(self) -> None:
        summary = self.selected_vm_summary()
        if summary is None:
            return
        self.run_action(f"Start {summary.vm_name}", self.service.start_vm, summary.vm_name)

    def handle_shutdown_selected_vm(self) -> None:
        summary = self.selected_vm_summary()
        if summary is None:
            return
        self.run_action(f"Shutdown {summary.vm_name}", self.service.shutdown_vm, summary.vm_name)

    def handle_enable_selected_vm(self) -> None:
        summary = self.selected_vm_summary()
        if summary is None or not summary.issue_number:
            return
        self.run_action(
            f"Enable agent {summary.vm_name}",
            self.service.enable_agent,
            summary.issue_number,
        )

    def handle_disable_selected_vm(self) -> None:
        summary = self.selected_vm_summary()
        if summary is None or not summary.issue_number:
            return
        self.run_action(
            f"Disable agent {summary.vm_name}",
            self.service.disable_agent,
            summary.issue_number,
        )

    def handle_resume_selected_vm(self) -> None:
        summary = self.selected_vm_summary()
        if summary is None:
            return
        self.resume_vm(summary)

    def handle_destroy_selected_vm(self) -> None:
        summary = self.selected_vm_summary()
        if summary is None:
            return
        self.confirm_destroy_vm(summary)

    def confirm_destroy_vm(self, summary) -> None:
        if not summary.issue_number:
            return
        answer = QMessageBox.question(
            self,
            "Destroy issue VM",
            f"Destroy {summary.vm_name} and remove its associated issue-VM state?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.run_action(
            f"Destroy {summary.vm_name}",
            self.service.destroy_issue_vm,
            summary.issue_number,
        )

    def resume_vm(self, summary) -> None:
        if not summary.issue_number:
            QMessageBox.information(self, "Resume agent", "Resume is supported only for issue-numbered VMs.")
            return
        prompt, accepted = QInputDialog.getMultiLineText(
            self,
            f"Resume {summary.vm_name}",
            "Prompt",
            "",
        )
        if not accepted or not prompt.strip():
            return
        self.run_action(
            f"Resume {summary.vm_name}",
            self.service.resume_agent,
            summary.issue_number,
            prompt.strip(),
        )

    def refresh_issues(self) -> None:
        previously_selected_issue = None
        current_issue = self.selected_issue_record()
        if current_issue is not None:
            previously_selected_issue = current_issue.issue_number
        try:
            issues = self.service.list_local_issues()
        except Exception as exc:
            self.append_log(f"Refresh issues failed: {exc}")
            return
        self._issue_records = issues
        self.issue_table.clearSelection()
        self.issue_table.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            self.issue_table.setItem(row, 0, QTableWidgetItem(issue.issue_number))
            self.issue_table.setItem(row, 1, QTableWidgetItem(issue.title))
            self.issue_table.setItem(row, 2, QTableWidgetItem(issue.repo_source or ""))
            self.issue_table.setItem(row, 3, QTableWidgetItem(issue.yaml_path))
        if previously_selected_issue:
            restored = False
            for row, issue in enumerate(issues):
                if issue.issue_number == previously_selected_issue:
                    self.issue_table.selectRow(row)
                    restored = True
                    break
            if not restored:
                self.issue_table.setCurrentCell(-1, -1)
        self.update_issue_action_bar()

    def refresh_workers(self) -> None:
        previously_selected_worker = None
        current_worker = self.selected_worker_summary()
        if current_worker is not None:
            previously_selected_worker = current_worker.worker_id
        try:
            workers = self.service.list_background_workers()
        except Exception as exc:
            self.append_log(f"Refresh workers failed: {exc}")
            return
        self._worker_summaries = workers
        self.worker_table.clearSelection()
        self.worker_table.setRowCount(len(workers))
        for row, worker in enumerate(workers):
            self.worker_table.setItem(row, 0, QTableWidgetItem(worker.name))
            self.worker_table.setItem(row, 1, QTableWidgetItem(worker.unit))
            self.worker_table.setItem(row, 2, QTableWidgetItem(worker.active_state))
            self.worker_table.setItem(row, 3, QTableWidgetItem(worker.sub_state or ""))
            self.worker_table.setItem(row, 4, QTableWidgetItem(worker.result or ""))
            self.worker_table.setItem(row, 5, QTableWidgetItem(str(worker.main_pid or "")))
            self.worker_table.setItem(row, 6, QTableWidgetItem(worker.unit_file_state or ""))
            self.worker_table.setItem(row, 7, QTableWidgetItem(worker.detail or ""))
        if previously_selected_worker:
            restored = False
            for row, worker in enumerate(workers):
                if worker.worker_id == previously_selected_worker:
                    self.worker_table.selectRow(row)
                    restored = True
                    break
            if not restored:
                self.worker_table.setCurrentCell(-1, -1)
        self.update_worker_action_bar()
        self.refresh_selected_worker_log()

    def show_new_issue_dialog(self) -> None:
        dialog = IssueDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        start_vm = bool(payload.pop("start_vm", False))

        def after_create(record) -> None:
            if start_vm:
                self.open_create_vm_dialog(record.issue_number, record.repo_source)

        self.run_action("Create local issue", self.service.create_local_issue, on_success=after_create, **payload)

    def open_create_vm_dialog(self, issue_number: str, repo_source: str | None = None) -> None:
        dialog = CreateVmDialog(issue_number, repo_source, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.run_action(
            f"Create VM for issue {issue_number}",
            self.service.create_issue_vm_from_local_issue,
            issue_number,
            **payload,
        )

    def confirm_delete_issue(self, issue_number: str) -> None:
        answer = QMessageBox.question(
            self,
            "Delete local issue",
            f"Delete local issue {issue_number}? This removes the stored YAML and local issue record.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.run_action(
            f"Delete issue {issue_number}",
            self.service.delete_local_issue,
            issue_number,
        )

    def selected_issue_record(self):
        row = self.issue_table.currentRow()
        if row < 0 or row >= len(self._issue_records):
            return None
        return self._issue_records[row]

    def update_issue_action_bar(self) -> None:
        issue = self.selected_issue_record()
        if issue is None:
            self.issue_selection_hint.setText("No issue selected")
            self.create_issue_vm_button.hide()
            self.delete_issue_button.hide()
            return

        self.issue_selection_hint.setText(f"Selected: {issue.issue_number}")
        self.create_issue_vm_button.show()
        self.delete_issue_button.show()

    def _on_issue_selection_changed(self) -> None:
        self.update_issue_action_bar()

    def handle_create_vm_selected_issue(self) -> None:
        issue = self.selected_issue_record()
        if issue is None:
            return
        self.open_create_vm_dialog(issue.issue_number, issue.repo_source)

    def handle_delete_selected_issue(self) -> None:
        issue = self.selected_issue_record()
        if issue is None:
            return
        self.confirm_delete_issue(issue.issue_number)

    def refresh_sessions(self) -> None:
        selected_session_id = None
        current_session = self.selected_session_summary()
        if current_session is not None:
            selected_session_id = current_session.session_id
        try:
            active_sessions = self.service.list_sessions()
            expired_sessions = self.service.list_sessions(expired_only=True)
        except Exception as exc:
            self.append_log(f"Refresh sessions failed: {exc}")
            return

        self._active_session_summaries = active_sessions
        self._expired_session_summaries = expired_sessions
        self.active_session_table.clearSelection()
        self.expired_session_table.clearSelection()
        self.active_session_table.setRowCount(len(active_sessions))
        self.expired_session_table.setRowCount(len(expired_sessions))

        for row, summary in enumerate(active_sessions):
            self.active_session_table.setItem(row, 0, QTableWidgetItem(_truncate_text(summary.session_id)))
            self.active_session_table.setItem(row, 1, QTableWidgetItem(summary.vm_id or ""))
            self.active_session_table.setItem(row, 2, QTableWidgetItem(summary.issue_number or ""))
            self.active_session_table.setItem(row, 3, QTableWidgetItem(summary.expires_at.isoformat()))

        for row, summary in enumerate(expired_sessions):
            self.expired_session_table.setItem(row, 0, QTableWidgetItem(_truncate_text(summary.session_id)))
            self.expired_session_table.setItem(row, 1, QTableWidgetItem(summary.vm_id or ""))
            self.expired_session_table.setItem(row, 2, QTableWidgetItem(summary.issue_number or ""))
            self.expired_session_table.setItem(row, 3, QTableWidgetItem(summary.expires_at.isoformat()))

        if selected_session_id:
            restored = False
            for row, summary in enumerate(active_sessions):
                if summary.session_id == selected_session_id:
                    self.session_tabs.setCurrentIndex(0)
                    self.active_session_table.selectRow(row)
                    restored = True
                    break
            if not restored:
                for row, summary in enumerate(expired_sessions):
                    if summary.session_id == selected_session_id:
                        self.session_tabs.setCurrentIndex(1)
                        self.expired_session_table.selectRow(row)
                        restored = True
                        break
            if not restored:
                self.active_session_table.setCurrentCell(-1, -1)
                self.expired_session_table.setCurrentCell(-1, -1)
        self.update_session_action_bar()
        self.refresh_selected_session_details()

    def _current_session_table(self):
        return self.active_session_table if self.session_tabs.currentIndex() == 0 else self.expired_session_table

    def selected_session_summary(self):
        table = self._current_session_table()
        summaries = self._active_session_summaries if table is self.active_session_table else self._expired_session_summaries
        row = table.currentRow()
        if row < 0 or row >= len(summaries):
            return None
        return summaries[row]

    def update_session_action_bar(self) -> None:
        summary = self.selected_session_summary()
        if summary is None:
            self.session_selection_hint.setText("No session selected")
            self.renew_session_button.hide()
            self.revoke_session_button.hide()
            self.delete_session_button.hide()
            return

        self.session_selection_hint.setText(f"Selected: {summary.session_id}")
        self.renew_session_button.show()
        self.revoke_session_button.show()
        self.delete_session_button.show()

    def refresh_selected_session_details(self) -> None:
        summary = self.selected_session_summary()
        if summary is None:
            _set_text_preserving_scroll(self.session_detail_view, "No session selected.")
            return
        _set_text_preserving_scroll(self.session_detail_view, _format_session_details(summary))

    def _on_active_session_selection_changed(self) -> None:
        if self.active_session_table.currentRow() >= 0 and self.expired_session_table.currentRow() >= 0:
            self.expired_session_table.blockSignals(True)
            self.expired_session_table.clearSelection()
            self.expired_session_table.blockSignals(False)
        self.update_session_action_bar()
        self.refresh_selected_session_details()

    def _on_expired_session_selection_changed(self) -> None:
        if self.expired_session_table.currentRow() >= 0 and self.active_session_table.currentRow() >= 0:
            self.active_session_table.blockSignals(True)
            self.active_session_table.clearSelection()
            self.active_session_table.blockSignals(False)
        self.update_session_action_bar()
        self.refresh_selected_session_details()

    def _on_session_tab_changed(self) -> None:
        self.update_session_action_bar()
        self.refresh_selected_session_details()

    def handle_renew_selected_session(self) -> None:
        summary = self.selected_session_summary()
        if summary is None:
            return
        extension, accepted = QInputDialog.getText(
            self,
            f"Renew {summary.session_id}",
            "Extend by",
            text="1d",
        )
        if not accepted or not extension.strip():
            return
        self.run_action(
            f"Renew session {summary.session_id}",
            self.service.renew_session,
            summary.session_id,
            extension=extension.strip(),
        )

    def handle_revoke_selected_session(self) -> None:
        summary = self.selected_session_summary()
        if summary is None:
            return
        answer = QMessageBox.question(
            self,
            "Revoke session",
            f"Expire session {summary.session_id} immediately?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.run_action(
            f"Revoke session {summary.session_id}",
            self.service.revoke_session,
            summary.session_id,
        )

    def handle_delete_selected_session(self) -> None:
        summary = self.selected_session_summary()
        if summary is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete session",
            f"Delete session {summary.session_id}?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.run_action(
            f"Delete session {summary.session_id}",
            self.service.delete_session,
            summary.session_id,
        )

    def selected_worker_summary(self):
        row = self.worker_table.currentRow()
        if row < 0 or row >= len(self._worker_summaries):
            return None
        return self._worker_summaries[row]

    def update_worker_selection_hint(self) -> None:
        worker = self.selected_worker_summary()
        if worker is None:
            self.worker_selection_hint.setText("No worker selected")
            self.start_worker_button.hide()
            self.stop_worker_button.hide()
            return
        self.worker_selection_hint.setText(f"Selected: {worker.name}")
        self.start_worker_button.setVisible(not worker.running)
        self.stop_worker_button.setVisible(worker.running)

    def refresh_selected_worker_log(self) -> None:
        worker = self.selected_worker_summary()
        if worker is None:
            _set_text_preserving_scroll(self.worker_log_view, "No worker selected.")
            return
        text = self.service.get_background_worker_log_snapshot(worker.worker_id, lines=200)
        _set_text_preserving_scroll(self.worker_log_view, text)

    def _on_worker_selection_changed(self) -> None:
        self.update_worker_action_bar()
        self.refresh_selected_worker_log()

    def update_worker_action_bar(self) -> None:
        self.update_worker_selection_hint()

    def handle_start_selected_worker(self) -> None:
        worker = self.selected_worker_summary()
        if worker is None:
            return
        self.run_action(
            f"Start worker {worker.name}",
            self.service.start_background_worker,
            worker.worker_id,
        )

    def handle_stop_selected_worker(self) -> None:
        worker = self.selected_worker_summary()
        if worker is None:
            return
        self.run_action(
            f"Stop worker {worker.name}",
            self.service.stop_background_worker,
            worker.worker_id,
        )


def main() -> None:
    if QApplication is None:  # pragma: no cover - depends on optional dependency.
        raise SystemExit(
            "PySide6 is not installed. Install the desktop extra first: "
            "uv pip install -e '.[desktop]'"
        )

    config_path = Path("config/gateway.yaml")
    argv = sys.argv[:]
    if len(argv) > 2 and argv[1] == "--config":
        config_path = Path(argv[2])
        del argv[1:3]

    app = QApplication(argv)
    service = AdminService(config_path)
    window = MainWindow(service)
    window.show()
    raise SystemExit(app.exec())
