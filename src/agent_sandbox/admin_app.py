from __future__ import annotations

import sys
from pathlib import Path

from .admin_service import AdminService

try:
    from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
    from PySide6.QtWidgets import (
        QApplication,
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
        QStatusBar,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover - exercised only when GUI extra is missing.
    QApplication = None
    QObject = object
    QRunnable = object
    QThreadPool = object
    QTimer = object
    Signal = lambda *args, **kwargs: None
    QCheckBox = QDialog = QDialogButtonBox = QFileDialog = QFormLayout = QHBoxLayout = object
    QHeaderView = QInputDialog = QLabel = QLineEdit = QMainWindow = QMessageBox = object
    QPushButton = QPlainTextEdit = QStatusBar = QTableWidget = QTableWidgetItem = object
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
        browse_button.clicked.connect(self._browse_yaml)
        yaml_layout.addWidget(browse_button)

        form.addRow("Title", self.title_edit)
        form.addRow("Repo source", self.repo_source_edit)
        form.addRow("Import YAML", yaml_row)
        form.addRow("Body", self.body_edit)
        layout.addLayout(form)
        layout.addWidget(self.start_vm_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_yaml(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Select issue YAML", "", "YAML Files (*.yaml *.yml)")
        if selected:
            self.yaml_path_edit.setText(selected)

    def payload(self) -> dict[str, object]:
        return {
            "title": self.title_edit.text().strip(),
            "body": self.body_edit.toPlainText().strip(),
            "repo_source": self.repo_source_edit.text().strip() or None,
            "yaml_path": self.yaml_path_edit.text().strip() or None,
            "start_vm": self.start_vm_checkbox.isChecked(),
        }


class MainWindow(QMainWindow):  # type: ignore[misc]
    def __init__(self, service: AdminService) -> None:
        super().__init__()
        self.service = service
        self.thread_pool = QThreadPool()
        self._active_workers: set[_ActionWorker] = set()
        self.setWindowTitle("Agent Sandbox Admin")
        self.resize(1360, 820)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.vm_table = QTableWidget(0, 8)
        self.vm_table.setHorizontalHeaderLabels(
            ["VM", "Issue", "Sessions", "Power", "IP", "Agent", "Repos", "Actions"]
        )
        self.vm_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self.issue_table = QTableWidget(0, 5)
        self.issue_table.setHorizontalHeaderLabels(["Issue", "Title", "Repo source", "YAML", "Actions"])
        self.issue_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)

        vm_page = QWidget()
        vm_layout = QVBoxLayout(vm_page)
        vm_toolbar = QHBoxLayout()
        refresh_vms_button = QPushButton("Refresh")
        refresh_vms_button.clicked.connect(self.refresh_vms)
        vm_toolbar.addWidget(refresh_vms_button)
        vm_toolbar.addStretch(1)
        vm_layout.addLayout(vm_toolbar)
        vm_layout.addWidget(self.vm_table)
        vm_layout.addWidget(QLabel("Operator log"))
        vm_layout.addWidget(self.log_view)

        issue_page = QWidget()
        issue_layout = QVBoxLayout(issue_page)
        issue_toolbar = QHBoxLayout()
        refresh_issues_button = QPushButton("Refresh")
        refresh_issues_button.clicked.connect(self.refresh_issues)
        new_issue_button = QPushButton("New Issue")
        new_issue_button.clicked.connect(self.show_new_issue_dialog)
        issue_toolbar.addWidget(new_issue_button)
        issue_toolbar.addWidget(refresh_issues_button)
        issue_toolbar.addStretch(1)
        issue_layout.addLayout(issue_toolbar)
        issue_layout.addWidget(self.issue_table)

        self.tabs.addTab(vm_page, "VMs")
        self.tabs.addTab(issue_page, "Issues")

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(5000)
        self.refresh_timer.timeout.connect(self.refresh_vms)
        self.refresh_timer.timeout.connect(self.refresh_issues)
        self.refresh_timer.start()

        self.refresh_vms()
        self.refresh_issues()

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

    def _handle_action_failure(self, label: str, error: str, worker: _ActionWorker) -> None:
        self._discard_worker(worker)
        self.append_log(f"{label} failed: {error}")
        QMessageBox.critical(self, "Action failed", error)

    def refresh_vms(self) -> None:
        try:
            summaries = self.service.list_vms(include_inactive=True, vm_user="agent")
        except Exception as exc:
            self.append_log(f"Refresh VMs failed: {exc}")
            return
        self.vm_table.setRowCount(len(summaries))
        for row, summary in enumerate(summaries):
            self.vm_table.setItem(row, 0, QTableWidgetItem(summary.vm_name))
            self.vm_table.setItem(row, 1, QTableWidgetItem(summary.issue_number or ""))
            self.vm_table.setItem(row, 2, QTableWidgetItem(", ".join(summary.session_ids)))
            self.vm_table.setItem(row, 3, QTableWidgetItem(summary.libvirt_state))
            self.vm_table.setItem(row, 4, QTableWidgetItem(summary.ip_address or ""))
            self.vm_table.setItem(row, 5, QTableWidgetItem(summary.agent_state))
            self.vm_table.setItem(row, 6, QTableWidgetItem(", ".join(summary.agent_repo_names)))
            self.vm_table.setCellWidget(row, 7, self._build_vm_actions(summary))

    def _build_vm_actions(self, summary) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        if summary.libvirt_state != "running":
            start_button = QPushButton("Start")
            start_button.clicked.connect(
                lambda: self.run_action(f"Start {summary.vm_name}", self.service.start_vm, summary.vm_name)
            )
            layout.addWidget(start_button)

        if summary.libvirt_state == "running":
            shutdown_button = QPushButton("Shutdown")
            shutdown_button.clicked.connect(
                lambda: self.run_action(f"Shutdown {summary.vm_name}", self.service.shutdown_vm, summary.vm_name)
            )
            layout.addWidget(shutdown_button)

        if summary.issue_number and summary.agent_state == "disabled":
            enable_button = QPushButton("Enable")
            enable_button.clicked.connect(
                lambda: self.run_action(
                    f"Enable agent {summary.vm_name}",
                    self.service.enable_agent,
                    summary.issue_number or summary.vm_name,
                )
            )
            layout.addWidget(enable_button)

        if summary.issue_number and summary.libvirt_state == "running" and summary.agent_state != "disabled":
            disable_button = QPushButton("Disable")
            disable_button.clicked.connect(
                lambda: self.run_action(
                    f"Disable agent {summary.vm_name}",
                    self.service.disable_agent,
                    summary.issue_number or summary.vm_name,
                )
            )
            layout.addWidget(disable_button)

        if summary.issue_number and summary.agent_state != "disabled":
            resume_button = QPushButton("Resume")
            resume_button.clicked.connect(lambda: self.resume_vm(summary))
            layout.addWidget(resume_button)

        return widget

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
        try:
            issues = self.service.list_local_issues()
        except Exception as exc:
            self.append_log(f"Refresh issues failed: {exc}")
            return
        self.issue_table.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            self.issue_table.setItem(row, 0, QTableWidgetItem(issue.issue_number))
            self.issue_table.setItem(row, 1, QTableWidgetItem(issue.title))
            self.issue_table.setItem(row, 2, QTableWidgetItem(issue.repo_source or ""))
            self.issue_table.setItem(row, 3, QTableWidgetItem(issue.yaml_path))
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(0, 0, 0, 0)
            start_button = QPushButton("Start VM")
            start_button.clicked.connect(
                lambda _checked=False, issue_number=issue.issue_number: self.run_action(
                    f"Create VM for issue {issue_number}",
                    self.service.create_issue_vm_from_local_issue,
                    issue_number,
                )
            )
            action_layout.addWidget(start_button)
            self.issue_table.setCellWidget(row, 4, actions)

    def show_new_issue_dialog(self) -> None:
        dialog = IssueDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()

        def after_create(record) -> None:
            if payload["start_vm"]:
                self.run_action(
                    f"Create VM for issue {record.issue_number}",
                    self.service.create_issue_vm_from_local_issue,
                    record.issue_number,
                )

        self.run_action("Create local issue", self.service.create_local_issue, on_success=after_create, **payload)


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
