from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from types import UnionType
from pathlib import Path
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from .admin_service import ISSUE_STATES, AdminService
from .config import (
    AdoWorkerGlobalConfig,
    GatewayConfig,
    HostHeartbeatWorkerGlobalConfig,
    IssueOrchestratorWorkerGlobalConfig,
)
from .directory_config import (
    AdoWorkerDirectoryConfig,
    DIRECTORY_CONFIG_DOMAIN_FIELDS,
    DIRECTORY_CONFIG_DOMAIN_FILENAMES,
    IssueSetupDirectoryConfig,
    PrTemplateDirectoryConfig,
)
from .models import ApprovalStatus
ADMIN_APP_DEFAULT_RUNTIME_DIR = Path("runtime")

try:
    from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, QTimer, Qt, Signal
    from PySide6.QtWidgets import (
        QApplication,
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
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
        QScrollArea,
        QSplitter,
        QStackedWidget,
        QSpinBox,
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
    class _FallbackTableWidgetItem:
        def __init__(self, text: str = "") -> None:
            self._text = text

        def text(self) -> str:
            return self._text

    QApplication = None
    QObject = object
    QRunnable = object
    QSettings = object
    QThreadPool = object
    QTimer = object
    Qt = object
    Signal = lambda *args, **kwargs: None
    QButtonGroup = object
    QCheckBox = QDialog = QDialogButtonBox = QFileDialog = QFormLayout = QHBoxLayout = object
    QHeaderView = QInputDialog = QLabel = QLineEdit = QMainWindow = QMessageBox = object
    QAbstractItemView = object
    QStyle = object
    QPushButton = QPlainTextEdit = QRadioButton = QScrollArea = QSplitter = QSpinBox = QDoubleSpinBox = object
    QComboBox = QStatusBar = QTableWidget = object
    QTableWidgetItem = _FallbackTableWidgetItem
    QToolButton = object
    QStackedWidget = object
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


class EditIssueDialog(QDialog):  # type: ignore[misc]
    def __init__(self, issue, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Local Issue {issue.issue_number}")
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.title_edit = QLineEdit(issue.title)
        self.repo_source_edit = QLineEdit(issue.repo_source or "")
        self.state_combo = QComboBox()
        self.state_combo.addItems(list(ISSUE_STATES))
        selected_state = issue.state or "New"
        selected_index = self.state_combo.findText(selected_state)
        if selected_index >= 0:
            self.state_combo.setCurrentIndex(selected_index)
        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlainText(issue.body or "")

        form.addRow("Title", self.title_edit)
        form.addRow("Repo source", self.repo_source_edit)
        form.addRow("State", self.state_combo)
        form.addRow("Body", self.body_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def payload(self) -> dict[str, object]:
        return {
            "title": self.title_edit.text().strip(),
            "repo_source": self.repo_source_edit.text().strip() or None,
            "state": self.state_combo.currentText().strip() or "New",
            "body": self.body_edit.toPlainText().strip(),
        }


class ReadOnlyTextDialog(QDialog):  # type: ignore[misc]
    def __init__(self, *, title: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 640)

        layout = QVBoxLayout(self)
        text_view = QPlainTextEdit()
        text_view.setReadOnly(True)
        text_view.setPlainText(text)
        layout.addWidget(text_view)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)


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


class CreateStandaloneVmDialog(QDialog):  # type: ignore[misc]
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create VM")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.vm_name_edit = QLineEdit()
        self.vm_name_edit.setPlaceholderText("lab-vm-1")
        self.session_id_edit = QLineEdit()
        self.session_id_edit.setPlaceholderText("Optional; generated when empty")
        self.repo_source_edit = QLineEdit()
        self.repo_source_edit.setPlaceholderText("/path/to/repo or git@host:repo.git")
        self.python_checkbox = QCheckBox("Bootstrap Python")
        self.dotnet_checkbox = QCheckBox("Bootstrap .NET")
        self.nuget_checkbox = QCheckBox("Bootstrap NuGet")
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
        self.python_hint_label = QLabel(
            "Python bootstrap installs into a cloned guest repo checkout, so it requires Repo source."
        )
        self.python_hint_label.setWordWrap(True)

        form.addRow("VM name", self.vm_name_edit)
        form.addRow("Session ID", self.session_id_edit)
        form.addRow("Repo source", self.repo_source_edit)
        form.addRow("", self.python_checkbox)
        form.addRow("", self.python_hint_label)
        form.addRow("", self.dotnet_checkbox)
        form.addRow("", self.nuget_checkbox)
        form.addRow("", self.playwright_checkbox)
        form.addRow("", self.mssql_checkbox)
        form.addRow("", self.mssql_hint_label)
        form.addRow("MSSQL init root", self.mssql_init_root_row)
        layout.addLayout(form)

        self.repo_source_edit.textChanged.connect(self._update_state)
        self.mssql_checkbox.toggled.connect(self._update_state)
        self._update_state()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_state(self) -> None:
        self.mssql_init_root_row.setVisible(self.mssql_checkbox.isChecked())
        repo_available = bool(self.repo_source_edit.text().strip())
        self.python_checkbox.setEnabled(repo_available)
        self.python_hint_label.setVisible(not repo_available)
        if not repo_available:
            self.python_checkbox.setChecked(False)

    def payload(self) -> dict[str, object]:
        return {
            "vm_name": self.vm_name_edit.text().strip(),
            "session_id": self.session_id_edit.text().strip() or None,
            "repo_source": self.repo_source_edit.text().strip() or None,
            "python": self.python_checkbox.isChecked(),
            "dotnet": self.dotnet_checkbox.isChecked(),
            "nuget": self.nuget_checkbox.isChecked(),
            "playwright": self.playwright_checkbox.isChecked(),
            "mssql": self.mssql_checkbox.isChecked(),
            "mssql_init_root": self.mssql_init_root_edit.text().strip() or None,
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


def _pretty_config_field_name(name: str) -> str:
    return name.replace("_", " ")


def _unwrap_optional_annotation(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin in {UnionType} or str(origin) == "typing.Union":
        non_none_args = tuple(arg for arg in get_args(annotation) if arg is not type(None))
        if len(non_none_args) == 1:
            return non_none_args[0]
    return annotation


def _is_string_list_annotation(annotation: Any) -> bool:
    annotation = _unwrap_optional_annotation(annotation)
    origin = get_origin(annotation)
    if origin is None:
        return False
    if origin not in {list, tuple} and str(origin) not in {"typing.List", "typing.Sequence"}:
        return False
    args = get_args(annotation)
    return len(args) == 1 and args[0] is str


CONFIG_FIELD_TOOLTIPS: dict[str, str] = {
    "default_session_ttl_seconds": (
        "Default session lifetime in seconds when a new session is created without an explicit TTL."
    ),
    "discord_operator_channel_allowlist": (
        "Channel IDs that the host Discord bot may poll for operator commands in addition to any "
        "session-specific Discord channel allowlists."
    ),
    "discord_operator_user_allowlist": (
        "Trusted Discord operator user IDs. Commands from these users are accepted even when they "
        "arrive outside a session's channel allowlist."
    ),
    "discord_poll_operator_dms": (
        "When enabled, the host Discord bot also polls direct messages from allowlisted operator users."
    ),
    "email_draft_domain_allowlist": (
        "Allowed recipient domains for draft and send operations. Recipients outside these domains "
        "are rejected by policy."
    ),
    "email_draft_recipient_allowlist": (
        "Allowed full recipient addresses for draft and send operations. If non-empty, every "
        "recipient must match this allowlist."
    ),
    "email_folder_allowlist_by_mailbox": (
        "JSON object mapping mailbox IDs to the folder names sessions may access. Used by session "
        "creation defaults and mailbox-specific policy checks."
    ),
    "email_mailbox_allowlist": (
        "Mailbox IDs that new sessions may access by default."
    ),
    "email_send_enabled": (
        "Enables actual email send operations. Leave disabled to keep email access in read-and-draft mode."
    ),
    "pr_reply_require_approval": (
        "Requires an approval request before posting PR feedback replies through the gateway."
    ),
    "redacted_keys": (
        "Request and response field names whose values should be redacted in gateway audit logs."
    ),
    "azure_devops_organization_url": (
        "Base Azure DevOps organization URL used by PR reply and feedback clients, for example "
        "https://dev.azure.com/<org>."
    ),
    "azure_devops_pat_env": (
        "Name of the host environment variable that holds the Azure DevOps personal access token."
    ),
    "azure_devops_pr_pat_env": (
        "Name of the host environment variable that holds the Azure DevOps PR personal access token."
    ),
    "ado_mssql_sa_password_env": (
        "Name of the host environment variable that holds the MSSQL SA password used by ADO issue setup."
    ),
    "ado_area_filters": (
        "Optional Azure DevOps area path allowlist used by the ADO worker when selecting work items."
    ),
    "ado_team_filters": (
        "Optional Azure DevOps team filter list used by the ADO worker when selecting work items."
    ),
    "ado_project_or_solution": (
        "Project or solution path handed to setup_issue_vm.sh for repo bootstrap and prewarm tasks."
    ),
    "ado_prewarm_target": (
        "Optional override target for prewarm steps; falls back to ado_project_or_solution when unset."
    ),
    "ado_repo_source": (
        "Host repo path or remote URL passed to setup_issue_vm.sh for guest repo mirroring."
    ),
    "ado_bootstrap_dotnet": (
        "Enable or disable dotnet/bootstrap steps in setup_issue_vm.sh for ADO worker-created VMs."
    ),
    "ado_bootstrap_nuget": (
        "Enable or disable NuGet bootstrap setup for ADO worker-created VMs."
    ),
    "ado_bootstrap_mssql": (
        "Enable or disable MSSQL bootstrap setup for ADO worker-created VMs."
    ),
    "ado_mssql_init_root": (
        "Optional host path containing SQL initialization scripts copied during MSSQL bootstrap."
    ),
    "ado_bootstrap_playwright": (
        "Enable or disable Playwright bootstrap setup for ADO worker-created VMs."
    ),
    "ado_bootstrap_frontend_deps": (
        "Enable or disable frontend dependency bootstrap setup for ADO worker-created VMs."
    ),
    "ado_frontend_host_checkout": (
        "Optional host checkout path used by frontend dependency bootstrap."
    ),
    "ado_frontend_install_roots": (
        "Optional list of install roots used by frontend dependency bootstrap."
    ),
    "azure_devops_project": (
        "Default Azure DevOps project name used by host-side PR feedback and reply integrations."
    ),
    "discord_api_base_url": (
        "Base URL for the Discord Bot API client used by the host runtime when discord_backend is bot_api."
    ),
    "discord_backend": (
        "Discord adapter mode. 'stub' uses the local JSON command store only; 'bot_api' talks to Discord's "
        "HTTP API from the host."
    ),
    "discord_bot_token_env": (
        "Name of the host environment variable that contains the Discord bot token for bot_api mode."
    ),
    "discord_poll_limit": (
        "Maximum number of Discord events fetched in each polling request."
    ),
    "discord_retry_base_backoff_seconds": (
        "Initial backoff in seconds between retried Discord API requests."
    ),
    "discord_retry_count": (
        "How many times the host Discord client retries a failed API request before giving up."
    ),
    "discord_retry_max_backoff_seconds": (
        "Upper bound in seconds for Discord API retry backoff."
    ),
    "discord_timeout_seconds": (
        "HTTP timeout in seconds for Discord API requests."
    ),
    "email_mailboxes": (
        "JSON array of mailbox definitions. Each entry supports mailbox_id, provider "
        "(json_store/gmail/graph), account or address, auth_env_var_names/auth_secret_refs, "
        "folder_mapping, and folder_allowlist_defaults."
    ),
    "email_provider_timeout_seconds": (
        "HTTP timeout in seconds for provider-backed email and PR reply clients."
    ),
    "github_token_env": (
        "Name of the host environment variable that holds the GitHub token for PR reply and feedback operations."
    ),
    "nuget_azure_feed_base_url": (
        "Base URL for the Azure Artifacts NuGet feed proxied by the host relay."
    ),
    "nuget_azure_pat_env": (
        "Name of the host environment variable that holds the Azure Artifacts PAT for the NuGet relay."
    ),
    "nuget_public_feed_base_url": (
        "Base URL for the public NuGet feed proxied by the host relay."
    ),
    "nuget_relay_timeout_seconds": (
        "Upstream request timeout in seconds for the NuGet relay."
    ),
    "openai_api_base_url": (
        "Upstream OpenAI-compatible API base URL used by the host relay."
    ),
    "openai_api_key_env": (
        "Name of the host environment variable that holds the upstream OpenAI API key."
    ),
    "openai_relay_timeout_seconds": (
        "Upstream request timeout in seconds for the OpenAI relay."
    ),
    "issue_setup_azure_devops_pr_pat_env": (
        "Optional override for the Azure DevOps PR PAT env-var name used by issue setup and PR scripts."
    ),
    "pr_template_markdown": (
        "Markdown boilerplate prepended to automated PR descriptions for this directory."
    ),
    "git_root": (
        "Host root directory that stores bare git remotes used during issue setup."
    ),
    "git_host": (
        "Host address or name the guest uses to reach git remotes during issue setup."
    ),
    "git_user": (
        "Host username used by guest SSH/git commands during issue setup."
    ),
    "git_ssh_alias": (
        "SSH host alias configured in the guest for host git operations."
    ),
    "post_exec_verification_prompt": (
        "Optional one-time follow-up prompt injected after the primary post-exec run."
    ),
    "enable_guest_heartbeat": (
        "When enabled, setup_issue_vm.sh writes guest heartbeat integration settings for Codex sessions."
    ),
    "guest_heartbeat_prompt": (
        "Prompt used by guest heartbeat wake-ups when enable_guest_heartbeat is true."
    ),
    "host_busy_check_script_path": (
        "Host script used by the heartbeat worker to detect whether a session VM is currently busy "
        "before attempting to resume it."
    ),
    "host_heartbeat_cooldown_seconds": (
        "Minimum cooldown in seconds before the heartbeat worker will try to resume the same session again."
    ),
    "host_heartbeat_http_timeout_seconds": (
        "HTTP client timeout in seconds for each host heartbeat feedback polling cycle."
    ),
    "host_heartbeat_jitter_seconds": (
        "Random delay added to each heartbeat sleep interval to avoid perfectly synchronized polling."
    ),
    "host_heartbeat_lock_path": (
        "Single-instance lock file path for the host heartbeat worker."
    ),
    "host_heartbeat_poll_interval_seconds": (
        "Base polling interval in seconds for the host heartbeat worker."
    ),
    "host_heartbeat_resume_prompt": (
        "Prompt text passed to the resume script when waking an agent session to handle newly arrived feedback."
    ),
    "host_heartbeat_resume_timeout_seconds": (
        "Timeout in seconds for the host resume script invocation."
    ),
    "host_heartbeat_session_cooldown_path": (
        "JSON state file path where the heartbeat worker tracks per-session cooldowns."
    ),
    "host_heartbeat_stream_conflict_policy": (
        "How the heartbeat worker handles one PR feedback stream matching multiple sessions: "
        "'fanout' polls anyway, 'reject' skips that stream."
    ),
    "host_heartbeat_vm_user": (
        "Guest username passed to the resume script when reconnecting to a VM."
    ),
    "host_heartbeat_wake_state_path": (
        "Host-side JSON state file written by the resume script to track recent wake attempts."
    ),
    "host_resume_script_path": (
        "Host script invoked by the heartbeat worker to resume an agent VM when unread feedback is available."
    ),
    "bind_network_cidr": (
        "Network CIDR assigned to the guest bind network. Must be a real network, for example 192.168.250.0/24."
    ),
    "gateway_url": (
        "MCP gateway URL exposed to guests and used by CLI health checks."
    ),
    "nuget_relay_host": (
        "Host address the NuGet relay listens on."
    ),
    "nuget_relay_port": (
        "Port the NuGet relay listens on."
    ),
    "openai_relay_host": (
        "Host address the OpenAI relay listens on."
    ),
    "openai_relay_port": (
        "Port the OpenAI relay listens on."
    ),
}

WORKER_GLOBAL_CONFIG_FIELD_TOOLTIPS: dict[str, str] = {
    "runtime_dir": "Base runtime directory for the ADO worker's state and default relative paths.",
    "poll_interval_seconds": "How often the ADO worker polls Azure DevOps for new work items.",
    "directories": (
        "Canonical directory paths the ADO worker should process on each cycle. "
        "One path per line; an empty list means the worker skips polling."
    ),
    "host_heartbeat_directory_paths": (
        "Canonical directory paths the host heartbeat worker should process on each cycle. "
        "One path per line; an empty list means the worker skips polling."
    ),
    "yaml_output_dir": "Directory where fetched work items are rendered as YAML before setup runs.",
    "setup_log_dir": "Directory where setup invocation logs are written.",
    "processed_store_path": "JSON state file used to remember work items that have already been handled.",
    "setup_script_path": "Host script invoked to provision an issue VM for each selected work item.",
    "ado_work_items_azure_devops_pat_env": "Name of the host environment variable that holds the Azure DevOps PAT used for ADO work-item polling.",
    "setup_timeout_seconds": "Timeout in seconds for the issue VM setup script.",
    "setup_concurrency_limit": "Maximum number of setup jobs that may run at once.",
    "setup_lock_path": "Lock file used to serialize setup work when concurrency is limited.",
    "fetch_retry_count": "How many times the worker retries Azure DevOps fetch calls.",
    "fetch_retry_backoff_seconds": "Backoff in seconds between Azure DevOps fetch retries.",
    "include_comments": "Whether fetched work item comments are attached to the YAML payloads.",
    "comments_max_count": "Maximum number of comments fetched per work item.",
    "comment_text_max_chars": "Maximum number of comment characters kept per comment.",
    "host_heartbeat_poll_interval_seconds": "Base polling interval in seconds for the host heartbeat worker.",
    "host_heartbeat_jitter_seconds": "Random delay added to each host heartbeat sleep interval.",
    "host_heartbeat_cooldown_seconds": "Minimum cooldown in seconds before the host heartbeat worker retries the same session.",
    "host_heartbeat_http_timeout_seconds": "HTTP client timeout in seconds for host heartbeat polling cycles.",
    "host_heartbeat_lock_path": "Single-instance lock file for the host heartbeat worker.",
    "host_heartbeat_session_cooldown_path": "JSON state file used by the host heartbeat worker for session cooldown tracking.",
    "host_busy_check_script_path": "Host script used to determine whether a session VM is busy before resuming it.",
    "host_resume_script_path": "Host script invoked to resume a session VM when feedback is waiting.",
    "host_heartbeat_wake_state_path": "Host-side JSON state file written by the resume workflow.",
    "host_heartbeat_vm_user": "Guest username passed to the host resume script.",
    "host_heartbeat_resume_timeout_seconds": "Timeout in seconds for the host resume script invocation.",
    "host_heartbeat_stream_conflict_policy": "How the host heartbeat worker handles one PR feedback stream matching multiple sessions.",
    "jitter_seconds": "Random delay added to each issue orchestrator sleep interval.",
    "cooldown_seconds": "Minimum cooldown before the issue orchestrator retries the same session.",
    "lock_path": "Single-instance lock file for the issue orchestrator worker.",
    "busy_check_script_path": "Host script used by the issue orchestrator to detect whether a session VM is busy.",
    "resume_script_path": "Host script invoked by the issue orchestrator to resume a session VM.",
    "wake_state_path": "Host-side JSON state file written by issue orchestrator resume attempts.",
    "vm_user": "Guest username passed to the issue orchestrator resume script.",
    "resume_timeout_seconds": "Timeout in seconds for an issue orchestrator resume invocation.",
}

WORKER_GLOBAL_CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "ado": AdoWorkerGlobalConfig,
    "host-heartbeat": HostHeartbeatWorkerGlobalConfig,
    "issue-orchestrator": IssueOrchestratorWorkerGlobalConfig,
}


def _format_config_value_preview(value: Any) -> str:
    if value is None:
        return "Inherited: null"
    if value == "":
        return "Inherited: \"\""
    try:
        rendered = json.dumps(value, sort_keys=True)
    except TypeError:
        rendered = str(value)
    return f"Inherited: {_truncate_text(rendered, max_length=120)}"


def _config_field_kind(field_name: str) -> tuple[str, tuple[Any, ...]]:
    field_models = (
        GatewayConfig.model_fields,
        AdoWorkerGlobalConfig.model_fields,
        HostHeartbeatWorkerGlobalConfig.model_fields,
        AdoWorkerDirectoryConfig.model_fields,
        IssueSetupDirectoryConfig.model_fields,
        PrTemplateDirectoryConfig.model_fields,
    )
    annotation = None
    for model_fields in field_models:
        field_info = model_fields.get(field_name)
        if field_info is not None:
            annotation = field_info.annotation
            break
    if annotation is None:
        return ("json", ())
    annotation = _unwrap_optional_annotation(annotation)
    origin = get_origin(annotation)
    if annotation is bool:
        return ("bool", ())
    if annotation is int:
        return ("int", ())
    if annotation is float:
        return ("float", ())
    if origin is not None and str(origin).endswith("Literal"):
        return ("literal", tuple(get_args(annotation)))
    if _is_string_list_annotation(annotation):
        return ("string_list", ())
    if annotation is str and (field_name.endswith("_prompt") or field_name.endswith("_markdown")):
        return ("multiline_text", ())
    if annotation in {str, Path}:
        return ("text", ())
    return ("json", ())


def _config_field_tooltip(field_name: str) -> str:
    return CONFIG_FIELD_TOOLTIPS.get(field_name, "")


def _worker_global_config_tooltip(field_name: str) -> str:
    return WORKER_GLOBAL_CONFIG_FIELD_TOOLTIPS.get(field_name, "")


def _root_config_field_starts_editable(domain: str, field_name: str, raw_config: dict[str, Any]) -> bool:
    return domain == "pr_template" and field_name == "pr_template_markdown" and field_name not in raw_config


def _parent_directory_path(path: str) -> str | None:
    if path == "root":
        return None
    segments = path.split("/")
    if len(segments) <= 1:
        return None
    return "/".join(segments[:-1]) or "root"


def _format_session_details(summary) -> str:
    payload = summary.model_dump(mode="json")
    payload["allowed_tools"] = [str(tool) for tool in summary.allowed_tools]
    return json.dumps(payload, indent=2, sort_keys=True)


class MainWindow(QMainWindow):  # type: ignore[misc]
    def __init__(self, service: AdminService) -> None:
        super().__init__()
        self.service = service
        self.service.use_privileged_helper = True
        self.settings = QSettings("agent-sandbox", "admin-app")
        self.thread_pool = QThreadPool()
        self._active_workers: set[_ActionWorker] = set()
        self._refresh_in_flight: set[str] = set()
        self._agent_log_request_token = 0
        self._worker_log_request_token = 0
        self._worker_global_config_request_token = 0
        self._config_request_token = 0
        self._config_form_loading = False
        self._config_dirty_domains: set[str] = set()
        self._worker_global_config_form_loading = False
        self._worker_global_config_dirty = False
        self._worker_global_config_worker_id: str | None = None
        self._vm_summaries = []
        self._issue_records = []
        self._approval_requests = []
        self._active_session_summaries = []
        self._expired_session_summaries = []
        self._worker_summaries = []
        self._selected_directory_path = "root"
        self.setWindowTitle("Agent Sandbox Admin")
        self.resize(1360, 820)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(8, 8, 8, 8)
        self.setCentralWidget(central)

        directory_toolbar = QHBoxLayout()
        self.selected_directory_label = QLabel("Selected directory: root")
        self.select_directory_button = QPushButton("Change")
        self.select_directory_button.clicked.connect(self.choose_directory)
        self.create_directory_button = QPushButton("Create Sub-directory")
        self.create_directory_button.clicked.connect(self.create_subdirectory)
        self.delete_directory_button = QPushButton("Delete Directory")
        self.delete_directory_button.clicked.connect(self.delete_selected_directory)
        self.unlock_privileges_button = QPushButton("Unlock")
        self.unlock_privileges_button.clicked.connect(self.handle_unlock_privileges)
        self.privilege_status_label = QLabel("Privileges: locked")
        directory_toolbar.addWidget(self.selected_directory_label)
        directory_toolbar.addWidget(self.select_directory_button)
        directory_toolbar.addWidget(self.create_directory_button)
        directory_toolbar.addWidget(self.delete_directory_button)
        directory_toolbar.addStretch(1)
        directory_toolbar.addWidget(self.privilege_status_label)
        directory_toolbar.addWidget(self.unlock_privileges_button)
        central_layout.addLayout(directory_toolbar)

        self.tabs = QTabWidget()
        central_layout.addWidget(self.tabs)
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

        self.issue_table = QTableWidget(0, 5)
        self.issue_table.setHorizontalHeaderLabels(["Issue", "Title", "State", "Repo source", "YAML"])
        self.issue_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.issue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.issue_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.issue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.issue_table.itemSelectionChanged.connect(self._on_issue_selection_changed)
        self.issue_table.itemDoubleClicked.connect(self._on_issue_activated)

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

        self.approval_table = QTableWidget(0, 8)
        self.approval_table.setHorizontalHeaderLabels(
            ["Request", "Status", "Session", "VM", "Tool", "Age", "Expires", "Summary"]
        )
        self.approval_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.approval_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.approval_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.approval_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.approval_table.itemSelectionChanged.connect(self._on_approval_selection_changed)
        self.approval_detail_view = QTextEdit()
        self.approval_detail_view.setReadOnly(True)
        self.approval_detail_view.setPlaceholderText(
            "Select an approval request to review its payload and audit context."
        )
        self.approval_health_label = QLabel("No approval data loaded.")
        self.approval_status_filter = QComboBox()
        self.approval_status_filter.addItems(["all", "pending", "approved", "denied", "expired"])
        self.approval_status_filter.currentIndexChanged.connect(self.refresh_approvals)
        self.approval_session_filter = QLineEdit()
        self.approval_session_filter.setPlaceholderText("Session filter")
        self.approval_vm_filter = QLineEdit()
        self.approval_vm_filter.setPlaceholderText("VM filter")
        self.approval_tool_filter = QLineEdit()
        self.approval_tool_filter.setPlaceholderText("Tool filter")
        self.approval_min_age_minutes = QSpinBox()
        self.approval_min_age_minutes.setRange(0, 1_000_000)
        self.approval_min_age_minutes.setSuffix(" min")
        self.approval_selection_hint = QLabel("No approval selected")
        self.approval_approve_button = QPushButton("Approve")
        self.approval_approve_button.clicked.connect(lambda: self.handle_decide_selected_approval(True))
        self.approval_deny_button = QPushButton("Deny")
        self.approval_deny_button.clicked.connect(lambda: self.handle_decide_selected_approval(False))

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
        self.worker_global_settings_status_label = QLabel(
            "Select a worker to edit global settings."
        )
        self.worker_global_settings_status_label.setWordWrap(True)
        self.worker_global_settings_form_scroll = QScrollArea()
        self.worker_global_settings_form_scroll.setWidgetResizable(True)
        self.worker_global_settings_form_stack = QStackedWidget()
        self.worker_global_settings_form_scroll.setWidget(self.worker_global_settings_form_stack)
        self._worker_global_config_widgets: dict[str, dict[str, dict[str, object]]] = {}
        self._worker_global_config_pages: dict[str, QWidget] = {}
        for worker_id, model in WORKER_GLOBAL_CONFIG_MODELS.items():
            page, widgets = self._build_worker_global_settings_page(worker_id, model)
            self.worker_global_settings_form_stack.addWidget(page)
            self._worker_global_config_widgets[worker_id] = widgets
            self._worker_global_config_pages[worker_id] = page
        self.worker_global_settings_save_button = QPushButton("Save")
        self.worker_global_settings_save_button.clicked.connect(self.save_worker_global_settings)
        self.worker_global_settings_reload_button = QPushButton("Reload")
        self.worker_global_settings_reload_button.clicked.connect(self.refresh_selected_worker_global_settings)
        self.worker_global_settings_button_row = QHBoxLayout()
        self.worker_global_settings_button_row.addWidget(self.worker_global_settings_save_button)
        self.worker_global_settings_button_row.addWidget(self.worker_global_settings_reload_button)
        self.worker_global_settings_button_row.addStretch(1)
        self.config_status_label = QLabel("No configuration loaded.")
        self.config_status_label.setWordWrap(True)
        self.config_tabs = QTabWidget()
        self._config_widgets: dict[str, dict[str, object]] = {}
        for domain in DIRECTORY_CONFIG_DOMAIN_FILENAMES:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            form_scroll = QScrollArea()
            form_scroll.setWidgetResizable(True)
            form_container = QWidget()
            form_layout = QFormLayout(form_container)
            form_scroll.setWidget(form_container)
            field_specs: dict[str, dict[str, object]] = {}
            for field_name in sorted(DIRECTORY_CONFIG_DOMAIN_FIELDS[domain]):
                editor = self._build_config_field_editor(domain, field_name)
                label = QLabel(_pretty_config_field_name(field_name))
                tooltip = _config_field_tooltip(field_name)
                if tooltip:
                    label.setToolTip(tooltip)
                form_layout.addRow(label, editor["container"])
                field_specs[field_name] = editor
            validation_view = QTextEdit()
            validation_view.setReadOnly(True)
            validation_view.setPlaceholderText("Validation diagnostics will appear here.")
            save_button = QPushButton("Save")
            save_button.clicked.connect(lambda _checked=False, domain=domain: self.save_config_domain(domain))
            view_parent_defaults_button = QPushButton("View Parent Defaults")
            view_parent_defaults_button.clicked.connect(
                lambda _checked=False, domain=domain: self.show_parent_defaults_dialog(domain)
            )
            view_effective_button = QPushButton("View Effective JSON")
            view_effective_button.clicked.connect(
                lambda _checked=False, domain=domain: self.show_effective_config_dialog(domain)
            )
            action_row = QHBoxLayout()
            action_row.addWidget(save_button)
            action_row.addWidget(view_parent_defaults_button)
            action_row.addWidget(view_effective_button)
            action_row.addStretch(1)
            page_layout.addWidget(QLabel("Overrides"))
            page_layout.addWidget(form_scroll)
            page_layout.addLayout(action_row)
            page_layout.addWidget(QLabel("Validation"))
            page_layout.addWidget(validation_view)
            self.config_tabs.addTab(page, domain)
            self._config_widgets[domain] = {
                "form_scroll": form_scroll,
                "field_specs": field_specs,
                "validation_view": validation_view,
                "save_button": save_button,
                "view_parent_defaults_button": view_parent_defaults_button,
                "view_effective_button": view_effective_button,
                "latest_parent_defaults_payload": None,
                "latest_effective_payload": None,
            }

        vm_page = QWidget()
        vm_layout = QVBoxLayout(vm_page)
        vm_toolbar = QHBoxLayout()
        refresh_vms_button = QPushButton("Refresh")
        _set_standard_icon(refresh_vms_button, "SP_BrowserReload")
        refresh_vms_button.clicked.connect(lambda: self.refresh_vms(manual=True))
        create_vm_button = QPushButton("New VM")
        _set_standard_icon(create_vm_button, "SP_FileDialogNewFolder")
        create_vm_button.clicked.connect(self.show_new_vm_dialog)
        vm_toolbar.addWidget(create_vm_button)
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
        self.view_issue_yaml_button = QPushButton("View YAML")
        _set_standard_icon(self.view_issue_yaml_button, "SP_FileDialogContentsView")
        self.view_issue_yaml_button.clicked.connect(self.handle_view_selected_issue_yaml)
        self.delete_issue_button = QPushButton("Delete")
        _set_standard_icon(self.delete_issue_button, "SP_TrashIcon")
        self.delete_issue_button.clicked.connect(self.handle_delete_selected_issue)
        self.edit_issue_button = QPushButton("Edit")
        _set_standard_icon(self.edit_issue_button, "SP_FileDialogDetailedView")
        self.edit_issue_button.clicked.connect(self.show_edit_issue_dialog)
        self.issue_action_bar.addWidget(self.create_issue_vm_button)
        self.issue_action_bar.addWidget(self.edit_issue_button)
        self.issue_action_bar.addWidget(self.view_issue_yaml_button)
        self.issue_action_bar.addWidget(self.delete_issue_button)
        issue_toolbar.addLayout(self.issue_action_bar)
        issue_layout.addLayout(issue_toolbar)
        issue_layout.addWidget(self.issue_table)

        approvals_page = QWidget()
        approvals_layout = QVBoxLayout(approvals_page)
        approvals_toolbar = QHBoxLayout()
        refresh_approvals_button = QPushButton("Refresh")
        _set_standard_icon(refresh_approvals_button, "SP_BrowserReload")
        refresh_approvals_button.clicked.connect(self.refresh_approvals)
        approvals_toolbar.addWidget(refresh_approvals_button)
        approvals_toolbar.addWidget(QLabel("Status"))
        approvals_toolbar.addWidget(self.approval_status_filter)
        approvals_toolbar.addWidget(self.approval_session_filter)
        approvals_toolbar.addWidget(self.approval_vm_filter)
        approvals_toolbar.addWidget(self.approval_tool_filter)
        approvals_toolbar.addWidget(QLabel("Min age"))
        approvals_toolbar.addWidget(self.approval_min_age_minutes)
        apply_approval_filters_button = QPushButton("Apply")
        apply_approval_filters_button.clicked.connect(self.refresh_approvals)
        approvals_toolbar.addWidget(apply_approval_filters_button)
        approvals_toolbar.addStretch(1)
        approvals_toolbar.addWidget(self.approval_selection_hint)
        approvals_toolbar.addWidget(self.approval_approve_button)
        approvals_toolbar.addWidget(self.approval_deny_button)
        approvals_layout.addLayout(approvals_toolbar)
        approvals_layout.addWidget(self.approval_health_label)
        approval_splitter = QSplitter(Qt.Orientation.Vertical)
        approval_splitter.addWidget(self.approval_table)
        approval_splitter.addWidget(self.approval_detail_view)
        approval_splitter.setStretchFactor(0, 1)
        approval_splitter.setStretchFactor(1, 1)
        approvals_layout.addWidget(approval_splitter)

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
        refresh_workers_button.clicked.connect(lambda: self.refresh_workers(manual=True))
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
        self.worker_splitter = worker_splitter
        worker_splitter.addWidget(self.worker_table)
        self.worker_details_panel = QWidget()
        worker_details_layout = QVBoxLayout(self.worker_details_panel)
        worker_details_layout.setContentsMargins(0, 0, 0, 0)
        self.worker_log_section = CollapsibleSection("Worker log", self.worker_log_view)
        self.worker_global_settings_section = CollapsibleSection(
            "Worker global settings",
            self._build_worker_global_settings_panel(),
        )
        worker_details_layout.addWidget(self.worker_log_section)
        worker_details_layout.addWidget(self.worker_global_settings_section)
        worker_splitter.addWidget(self.worker_details_panel)
        worker_splitter.setStretchFactor(0, 1)
        worker_splitter.setStretchFactor(1, 1)
        worker_layout.addWidget(worker_splitter)
        self.worker_log_section.toggle_button.toggled.connect(self._update_worker_splitter_layout)
        self.worker_global_settings_section.toggle_button.toggled.connect(self._update_worker_splitter_layout)

        config_page = QWidget()
        config_layout = QVBoxLayout(config_page)
        config_layout.addWidget(self.config_status_label)
        config_layout.addWidget(self.config_tabs)

        self.tabs.addTab(vm_page, "VMs")
        self.tabs.addTab(issue_page, "Issues")
        self.tabs.addTab(approvals_page, "Approvals")
        self.tabs.addTab(sessions_page, "Sessions")
        self.tabs.addTab(config_page, "Configuration")
        self.tabs.addTab(worker_page, "Workers")
        self.tabs.currentChanged.connect(self._on_main_tab_changed)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(5000)
        self.refresh_timer.timeout.connect(self._refresh_visible_data)
        self.refresh_timer.timeout.connect(self.refresh_issues)
        self.refresh_timer.timeout.connect(self.refresh_approvals)
        self.refresh_timer.timeout.connect(self.refresh_sessions)
        self.refresh_timer.start()

        self._restore_selected_directory()
        self._apply_directory_feature_state()
        self._refresh_visible_data()
        self.refresh_issues()
        self.refresh_approvals()
        self.refresh_sessions()
        self.refresh_configuration()
        self.refresh_workers()
        self.update_vm_action_bar()
        self.update_issue_action_bar()
        self.update_approval_action_bar()
        self.update_session_action_bar()
        self.update_worker_action_bar()
        self._update_vm_splitter_layout()
        self._update_privilege_status()

    def append_log(self, message: str) -> None:
        self.log_view.append(message)
        self.status_bar.showMessage(message, 8000)

    def _update_privilege_status(self) -> None:
        unlocked = self.service.privileged_operations_unlocked()
        self.privilege_status_label.setText("Privileges: unlocked" if unlocked else "Privileges: locked")
        self.unlock_privileges_button.setEnabled(not unlocked)

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

    def _show_action_error_dialog(self, message: str) -> None:
        self._show_read_only_text_dialog(title="Action Failed", text=message)

    def _handle_action_success(self, label: str, result, on_success, worker: _ActionWorker) -> None:
        self._discard_worker(worker)
        if getattr(result, "success", True) is False:
            message = getattr(result, "message", str(result))
            self.append_log(f"{label} failed: {message}")
            self._update_privilege_status()
            self._refresh_visible_data(manual=True)
            self.refresh_issues()
            self.refresh_approvals()
            self.refresh_workers()
            self._show_action_error_dialog(message)
            return
        message = getattr(result, "message", str(result))
        self.append_log(f"{label}: {message}")
        self._update_privilege_status()
        if on_success is not None:
            on_success(result)
        self._refresh_visible_data(manual=True)
        self.refresh_issues()
        self.refresh_approvals()
        self.refresh_workers()

    def _handle_action_failure(self, label: str, error: str, worker: _ActionWorker) -> None:
        self._discard_worker(worker)
        self.append_log(f"{label} failed: {error}")
        self._update_privilege_status()
        self._show_action_error_dialog(error)

    def handle_unlock_privileges(self) -> None:
        self.run_action("Unlock privileged operations", self.service.unlock_privileged_operations)

    def _start_refresh(self, key: str, fn, on_success) -> None:
        if key in self._refresh_in_flight:
            return
        worker = _ActionWorker(fn)
        self._active_workers.add(worker)
        self._refresh_in_flight.add(key)
        worker.signals.finished.connect(
            lambda result, worker=worker, key=key, on_success=on_success: self._handle_refresh_success(
                result, worker, key, on_success
            )
        )
        worker.signals.failed.connect(
            lambda error, worker=worker, key=key: self._handle_refresh_failure(error, worker, key)
        )
        self.thread_pool.start(worker)

    def _handle_refresh_success(self, result, worker: _ActionWorker, key: str, on_success) -> None:
        self._discard_worker(worker)
        self._refresh_in_flight.discard(key)
        on_success(result)

    def _handle_refresh_failure(self, error: str, worker: _ActionWorker, key: str) -> None:
        self._discard_worker(worker)
        self._refresh_in_flight.discard(key)
        self.append_log(f"Refresh {key} failed: {error}")

    def _refresh_visible_data(self, *, manual: bool = False) -> None:
        self.refresh_vms(manual=manual)
        if self.tabs.currentWidget() is not None and self.tabs.tabText(self.tabs.currentIndex()) == "Workers":
            self.refresh_workers(manual=manual)
        if self.tabs.currentWidget() is not None and self.tabs.tabText(self.tabs.currentIndex()) == "Approvals":
            self.refresh_approvals()
        if self.tabs.currentWidget() is not None and self.tabs.tabText(self.tabs.currentIndex()) == "Configuration":
            self.refresh_configuration()

    def _on_main_tab_changed(self) -> None:
        current_tab = self.tabs.tabText(self.tabs.currentIndex())
        if current_tab == "Configuration":
            self.refresh_configuration()
        elif current_tab == "Approvals":
            self.refresh_approvals()
        elif current_tab == "Workers":
            self.refresh_workers(manual=True)

    def closeEvent(self, event) -> None:
        self.service.lock_privileged_operations()
        super().closeEvent(event)

    def refresh_vms(self, *, manual: bool = False) -> None:
        self._start_refresh(
            "vms",
            lambda: self.service.list_vms(
                include_inactive=True,
                vm_user="agent",
                directory_path=self._selected_directory_path,
                direct_only=True,
            ),
            lambda summaries: self._apply_vm_refresh(summaries),
        )

    def _apply_vm_refresh(self, summaries) -> None:
        previously_selected_vm = None
        current_summary = self.selected_vm_summary()
        if current_summary is not None:
            previously_selected_vm = current_summary.vm_name
        self._vm_summaries = summaries
        self.vm_table.blockSignals(True)
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
        self.vm_table.blockSignals(False)
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
        supports_agent_controls = bool(summary.vm_name) and summary.agent_state != "unknown"
        self.enable_agent_button.setVisible(supports_agent_controls and summary.agent_state == "disabled")
        self.disable_agent_button.setVisible(supports_agent_controls and summary.agent_state != "disabled")
        self.resume_agent_button.setVisible(supports_agent_controls and summary.agent_state != "disabled")
        self.destroy_vm_button.setVisible(True)

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
        repo_name = self._preferred_agent_log_repo_name(summary)
        self._agent_log_request_token += 1
        request_token = self._agent_log_request_token
        worker = _ActionWorker(self.service.get_agent_log_snapshot, summary.vm_name, lines=50, repo_name=repo_name)
        self._active_workers.add(worker)
        worker.signals.finished.connect(
            lambda text, worker=worker, request_token=request_token, vm_name=summary.vm_name: self._handle_agent_log_success(
                text, worker, request_token, vm_name
            )
        )
        worker.signals.failed.connect(
            lambda error, worker=worker, request_token=request_token: self._handle_agent_log_failure(
                error, worker, request_token
            )
        )
        self.thread_pool.start(worker)

    def _handle_agent_log_success(
        self, text: str, worker: _ActionWorker, request_token: int, vm_name: str
    ) -> None:
        self._discard_worker(worker)
        current_summary = self.selected_vm_summary()
        if request_token != self._agent_log_request_token:
            return
        if current_summary is None or current_summary.vm_name != vm_name:
            return
        _set_text_preserving_scroll(self.agent_log_view, text)

    def _handle_agent_log_failure(self, error: str, worker: _ActionWorker, request_token: int) -> None:
        self._discard_worker(worker)
        if request_token != self._agent_log_request_token:
            return
        _set_text_preserving_scroll(self.agent_log_view, f"Agent log unavailable: {error}")

    @staticmethod
    def _preferred_agent_log_repo_name(summary) -> str | None:
        repo_names = [str(name).strip() for name in getattr(summary, "agent_repo_names", []) if str(name).strip()]
        if len(repo_names) == 1:
            return repo_names[0]
        return None

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
        if summary is None:
            return
        self.run_action(
            f"Enable agent {summary.vm_name}",
            self.service.enable_agent,
            summary.vm_name,
        )

    def handle_disable_selected_vm(self) -> None:
        summary = self.selected_vm_summary()
        if summary is None:
            return
        self.run_action(
            f"Disable agent {summary.vm_name}",
            self.service.disable_agent,
            summary.vm_name,
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
        title = "Destroy issue VM" if summary.issue_number else "Destroy VM"
        prompt = (
            f"Destroy {summary.vm_name} and remove its associated issue-VM state?"
            if summary.issue_number
            else f"Destroy {summary.vm_name} and remove its local session state?"
        )
        answer = QMessageBox.question(
            self,
            title,
            prompt,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.run_action(
            f"Destroy {summary.vm_name}",
            self.service.destroy_vm,
            summary.vm_name,
        )

    def resume_vm(self, summary) -> None:
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
            summary.vm_name,
            prompt.strip(),
        )

    def refresh_issues(self) -> None:
        self._start_refresh(
            "issues",
            lambda: self.service.list_local_issues(directory_path=self._selected_directory_path, direct_only=True),
            self._apply_issue_refresh,
        )

    def _apply_issue_refresh(self, issues) -> None:
        previously_selected_issue = None
        current_issue = self.selected_issue_record()
        if current_issue is not None:
            previously_selected_issue = current_issue.issue_number
        self._issue_records = issues
        self.issue_table.blockSignals(True)
        self.issue_table.clearSelection()
        self.issue_table.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            self.issue_table.setItem(row, 0, QTableWidgetItem(issue.issue_number))
            self.issue_table.setItem(row, 1, QTableWidgetItem(issue.title))
            self.issue_table.setItem(row, 2, QTableWidgetItem(issue.state or ""))
            self.issue_table.setItem(row, 3, QTableWidgetItem(issue.repo_source or ""))
            self.issue_table.setItem(row, 4, QTableWidgetItem(issue.yaml_path))
        if previously_selected_issue:
            restored = False
            for row, issue in enumerate(issues):
                if issue.issue_number == previously_selected_issue:
                    self.issue_table.selectRow(row)
                    restored = True
                    break
            if not restored:
                self.issue_table.setCurrentCell(-1, -1)
        self.issue_table.blockSignals(False)
        self.update_issue_action_bar()

    def _selected_approval_status_filter(self) -> set[ApprovalStatus] | None:
        status_text = self.approval_status_filter.currentText().strip().lower()
        if not status_text or status_text == "all":
            return None
        return {ApprovalStatus(status_text)}

    def refresh_approvals(self) -> None:
        min_age_minutes = self.approval_min_age_minutes.value()
        min_age_seconds = min_age_minutes * 60 if min_age_minutes > 0 else None
        self._start_refresh(
            "approvals",
            lambda: (
                self.service.approval_queue_health(
                    statuses=self._selected_approval_status_filter(),
                    directory_path=self._selected_directory_path,
                    direct_only=True,
                    session_id=self.approval_session_filter.text().strip() or None,
                    vm_id=self.approval_vm_filter.text().strip() or None,
                    tool_name=self.approval_tool_filter.text().strip() or None,
                    min_age_seconds=min_age_seconds,
                ),
                self.service.list_approval_requests(
                    statuses=self._selected_approval_status_filter(),
                    directory_path=self._selected_directory_path,
                    direct_only=True,
                    session_id=self.approval_session_filter.text().strip() or None,
                    vm_id=self.approval_vm_filter.text().strip() or None,
                    tool_name=self.approval_tool_filter.text().strip() or None,
                    min_age_seconds=min_age_seconds,
                ),
            ),
            self._apply_approval_refresh,
        )

    def _apply_approval_refresh(self, result) -> None:
        health, approvals = result
        previously_selected_request = None
        current_request = self.selected_approval_request()
        if current_request is not None:
            previously_selected_request = current_request.request_id
        self._approval_requests = approvals
        self.approval_health_label.setText(
            "Queue "
            f"total={health.total} pending={health.pending} approved={health.approved} "
            f"denied={health.denied} expired={health.expired} "
            f"oldest_pending_age_seconds={health.oldest_pending_age_seconds}"
        )
        self.approval_table.blockSignals(True)
        self.approval_table.clearSelection()
        self.approval_table.setRowCount(len(approvals))
        for row, request in enumerate(approvals):
            age_seconds = int((datetime.now(UTC) - request.created_at).total_seconds())
            expires_in_seconds = int((request.expires_at - datetime.now(UTC)).total_seconds())
            self.approval_table.setItem(row, 0, QTableWidgetItem(request.request_id))
            self.approval_table.setItem(row, 1, QTableWidgetItem(request.status.value))
            self.approval_table.setItem(row, 2, QTableWidgetItem(request.session_id))
            self.approval_table.setItem(row, 3, QTableWidgetItem(request.vm_id or ""))
            self.approval_table.setItem(row, 4, QTableWidgetItem(request.tool_name or request.action_type))
            self.approval_table.setItem(row, 5, QTableWidgetItem(f"{age_seconds}s"))
            self.approval_table.setItem(row, 6, QTableWidgetItem(f"{expires_in_seconds}s"))
            self.approval_table.setItem(row, 7, QTableWidgetItem(request.human_summary))
        if previously_selected_request:
            restored = False
            for row, request in enumerate(approvals):
                if request.request_id == previously_selected_request:
                    self.approval_table.selectRow(row)
                    restored = True
                    break
            if not restored:
                self.approval_table.setCurrentCell(-1, -1)
        self.approval_table.blockSignals(False)
        self.update_approval_action_bar()
        self.refresh_selected_approval_detail()

    def refresh_workers(self, *, manual: bool = False) -> None:
        self._start_refresh("workers", self.service.list_background_workers, lambda workers: self._apply_worker_refresh(workers, refresh_log=manual))

    def _apply_worker_refresh(self, workers, *, refresh_log: bool) -> None:
        previously_selected_worker = None
        current_worker = self.selected_worker_summary()
        if current_worker is not None:
            previously_selected_worker = current_worker.worker_id
        self._worker_summaries = workers
        self.worker_table.blockSignals(True)
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
        self.worker_table.blockSignals(False)
        self.update_worker_action_bar()
        if refresh_log:
            self.refresh_selected_worker_log()

    def show_new_issue_dialog(self) -> None:
        dialog = IssueDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        start_vm = bool(payload.pop("start_vm", False))
        payload["directory_path"] = self._selected_directory_path

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

    def show_new_vm_dialog(self) -> None:
        dialog = CreateStandaloneVmDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        vm_name = str(payload.pop("vm_name", "") or "").strip()
        if not vm_name:
            QMessageBox.warning(self, "Create VM", "VM name is required.")
            return
        self.run_action(
            f"Create VM {vm_name}",
            self.service.create_vm,
            directory_path=self._selected_directory_path,
            vm_name=vm_name,
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

    def selected_approval_request(self):
        row = self.approval_table.currentRow()
        if row < 0 or row >= len(self._approval_requests):
            return None
        return self._approval_requests[row]

    def _show_read_only_text_dialog(self, *, title: str, text: str) -> None:
        ReadOnlyTextDialog(title=title, text=text, parent=self).exec()

    def update_issue_action_bar(self) -> None:
        issue = self.selected_issue_record()
        if issue is None:
            self.issue_selection_hint.setText("No issue selected")
            self.create_issue_vm_button.hide()
            self.edit_issue_button.hide()
            self.view_issue_yaml_button.hide()
            self.delete_issue_button.hide()
            return

        self.issue_selection_hint.setText(f"Selected: {issue.issue_number}")
        self.create_issue_vm_button.show()
        self.edit_issue_button.setVisible(issue.source_type == "local_manual")
        self.view_issue_yaml_button.show()
        self.delete_issue_button.show()

    def update_approval_action_bar(self) -> None:
        request = self.selected_approval_request()
        if request is None:
            self.approval_selection_hint.setText("No approval selected")
            self.approval_approve_button.hide()
            self.approval_deny_button.hide()
            return
        self.approval_selection_hint.setText(f"Selected: {request.request_id}")
        is_pending = request.status == ApprovalStatus.PENDING
        self.approval_approve_button.setVisible(is_pending)
        self.approval_deny_button.setVisible(is_pending)

    def _on_issue_selection_changed(self) -> None:
        self.update_issue_action_bar()

    def _on_issue_activated(self, _item) -> None:
        self.handle_view_selected_issue_yaml()

    def _on_approval_selection_changed(self) -> None:
        self.update_approval_action_bar()
        self.refresh_selected_approval_detail()

    def handle_create_vm_selected_issue(self) -> None:
        issue = self.selected_issue_record()
        if issue is None:
            return
        self.open_create_vm_dialog(issue.issue_number, issue.repo_source)

    def show_edit_issue_dialog(self) -> None:
        issue = self.selected_issue_record()
        if issue is None or issue.source_type != "local_manual":
            return
        dialog = EditIssueDialog(issue, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        self.run_action(
            f"Edit issue {issue.issue_number}",
            self.service.update_local_issue,
            issue.issue_number,
            **payload,
        )

    def handle_view_selected_issue_yaml(self) -> None:
        issue = self.selected_issue_record()
        if issue is None:
            return
        yaml_path = Path(issue.yaml_path)
        try:
            yaml_text = yaml_path.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "View issue YAML", f"Could not read {yaml_path}: {exc}")
            return
        self._show_read_only_text_dialog(
            title=f"Issue YAML: {issue.issue_number}",
            text=yaml_text,
        )

    def handle_delete_selected_issue(self) -> None:
        issue = self.selected_issue_record()
        if issue is None:
            return
        self.confirm_delete_issue(issue.issue_number)

    def refresh_selected_approval_detail(self) -> None:
        request = self.selected_approval_request()
        if request is None:
            _set_text_preserving_scroll(self.approval_detail_view, "No approval selected.")
            return
        try:
            payload = self.service.get_approval_review(request.request_id)
        except Exception as exc:
            _set_text_preserving_scroll(self.approval_detail_view, f"Approval details unavailable: {exc}")
            return
        _set_text_preserving_scroll(self.approval_detail_view, json.dumps(payload, indent=2, sort_keys=True))

    def handle_decide_selected_approval(self, approve: bool) -> None:
        request = self.selected_approval_request()
        if request is None:
            return
        approver, accepted = QInputDialog.getText(
            self,
            "Approval Decision",
            "Approver",
        )
        if not accepted or not str(approver or "").strip():
            return
        reason, accepted = QInputDialog.getMultiLineText(
            self,
            "Approval Decision",
            "Reason",
        )
        if not accepted or not str(reason or "").strip():
            return
        action_label = "Approve" if approve else "Deny"
        self.run_action(
            f"{action_label} approval {request.request_id}",
            self.service.decide_approval_request,
            request.request_id,
            approver=str(approver).strip(),
            approve=approve,
            reason=str(reason).strip(),
        )

    def refresh_sessions(self) -> None:
        self._start_refresh(
            "sessions",
            lambda: self.service.list_sessions(
                include_expired=True,
                directory_path=self._selected_directory_path,
                direct_only=True,
            ),
            self._apply_session_refresh,
        )

    def _restore_selected_directory(self) -> None:
        stored = self.settings.value("selected_directory", "root")
        self._set_selected_directory(str(stored or "root"), refresh=False)

    def _set_selected_directory(self, directory_path: str, *, refresh: bool = True) -> None:
        result = self.service.get_directory(directory_path)
        if not result.ok or result.data is None:
            directory_path = "root"
            result = self.service.get_directory("root")
        self._selected_directory_path = result.data.details.directory.path if result.ok and result.data else "root"
        self.selected_directory_label.setText(f"Selected directory: {self._selected_directory_path}")
        self.settings.setValue("selected_directory", self._selected_directory_path)
        self._update_directory_action_bar()
        if refresh:
            self.refresh_vms(manual=True)
            self.refresh_issues()
            self.refresh_sessions()
            self.refresh_configuration()

    def _update_directory_action_bar(self) -> None:
        edits_enabled = self.service.directory_scoped_config_enabled
        self.create_directory_button.setEnabled(edits_enabled)
        self.delete_directory_button.setEnabled(edits_enabled and self._selected_directory_path != "root")

    def _build_config_field_editor(self, domain: str, field_name: str) -> dict[str, object]:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        tooltip = _config_field_tooltip(field_name)

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(6)
        include_checkbox = QCheckBox("Override")
        clear_checkbox = QCheckBox("Clear")
        kind, literal_choices = _config_field_kind(field_name)
        if kind == "bool":
            control = QComboBox()
            control.addItem("false")
            control.addItem("true")
            control.setMaximumWidth(90)
        elif kind == "int":
            control = QSpinBox()
            control.setRange(-1_000_000_000, 1_000_000_000)
            control.setMaximumWidth(140)
        elif kind == "float":
            control = QDoubleSpinBox()
            control.setRange(-1_000_000_000.0, 1_000_000_000.0)
            control.setDecimals(6)
            control.setMaximumWidth(160)
        elif kind == "literal":
            control = QComboBox()
            for choice in literal_choices:
                control.addItem(str(choice))
            control.setMaximumWidth(220)
        elif kind == "text":
            control = QLineEdit()
            control.setMaximumWidth(360)
        elif kind == "multiline_text":
            control = QPlainTextEdit()
            control.setPlaceholderText("Text value")
            control.setFixedHeight(96)
        elif kind == "string_list":
            control = QPlainTextEdit()
            control.setPlaceholderText("One item per line")
            control.setFixedHeight(96)
        else:
            control = QPlainTextEdit()
            control.setPlaceholderText("JSON value")
            control.setMaximumBlockCount(0)
            control.setFixedHeight(84)

        inherited_label = QLabel("Inherited: not loaded yet")
        inherited_label.setWordWrap(False)
        inherited_label.setMinimumWidth(160)
        if tooltip:
            container.setToolTip(tooltip)
            control.setToolTip(tooltip)
            inherited_label.setToolTip(tooltip)
            include_checkbox.setToolTip(f"{tooltip}\n\nEnable to set a directory-specific override for this field.")
            if kind == "string_list":
                clear_checkbox.setToolTip(
                    f"{tooltip}\n\nEnable with Override to clear the inherited list value."
                )
            else:
                clear_checkbox.setToolTip(
                    f"{tooltip}\n\nEnable with Override to write an explicit null and clear the inherited value."
                )

        if kind == "json":
            toggle_group = QHBoxLayout()
            toggle_group.setContentsMargins(0, 0, 0, 0)
            toggle_group.setSpacing(6)
            toggle_group.addWidget(include_checkbox)
            toggle_group.addWidget(clear_checkbox)
            controls_row.addWidget(inherited_label, 1)
            controls_row.addStretch(1)
            controls_row.addLayout(toggle_group)
            layout.addLayout(controls_row)
            layout.addWidget(control)
        else:
            controls_row.addWidget(control)
            controls_row.addWidget(inherited_label)
            controls_row.addStretch(1)
            toggle_group = QHBoxLayout()
            toggle_group.setContentsMargins(0, 0, 0, 0)
            toggle_group.setSpacing(6)
            toggle_group.addWidget(include_checkbox)
            toggle_group.addWidget(clear_checkbox)
            controls_row.addLayout(toggle_group)
            layout.addLayout(controls_row)

        spec = {
            "container": container,
            "kind": kind,
            "control": control,
            "include_checkbox": include_checkbox,
            "clear_checkbox": clear_checkbox,
            "inherited_label": inherited_label,
            "root_mode": False,
        }
        self._update_config_field_control_value(spec, None)
        self._update_config_field_enabled_state_from_spec(spec)
        include_checkbox.toggled.connect(lambda _checked=False, domain=domain: self._mark_config_domain_dirty(domain))
        include_checkbox.toggled.connect(
            lambda _checked=False, domain=domain, field_name=field_name: self._update_config_field_enabled_state(
                domain, field_name
            )
        )
        clear_checkbox.toggled.connect(
            lambda _checked=False, domain=domain, field_name=field_name: self._handle_config_field_clear_toggled(
                domain, field_name
            )
        )
        if kind == "bool":
            control.currentTextChanged.connect(lambda _value, domain=domain: self._mark_config_domain_dirty(domain))
        elif kind in {"int", "float"}:
            control.valueChanged.connect(lambda _value, domain=domain: self._mark_config_domain_dirty(domain))
        elif kind == "literal":
            control.currentTextChanged.connect(lambda _value, domain=domain: self._mark_config_domain_dirty(domain))
        elif kind == "text":
            control.textChanged.connect(lambda _value, domain=domain: self._mark_config_domain_dirty(domain))
        elif kind == "multiline_text":
            control.textChanged.connect(lambda domain=domain: self._mark_config_domain_dirty(domain))
        elif kind == "string_list":
            control.textChanged.connect(lambda domain=domain: self._mark_config_domain_dirty(domain))
        else:
            control.textChanged.connect(lambda domain=domain: self._mark_config_domain_dirty(domain))
        return spec

    def _mark_config_domain_dirty(self, domain: str) -> None:
        if self._config_form_loading:
            return
        self._config_dirty_domains.add(domain)

    def _update_config_field_enabled_state(self, domain: str, field_name: str) -> None:
        spec = self._config_widgets[domain]["field_specs"][field_name]
        self._update_config_field_enabled_state_from_spec(spec)

    def _handle_config_field_clear_toggled(self, domain: str, field_name: str) -> None:
        spec = self._config_widgets[domain]["field_specs"][field_name]
        if spec.get("root_mode"):
            spec["include_checkbox"].setChecked(not spec["clear_checkbox"].isChecked())
        self._mark_config_domain_dirty(domain)
        self._update_config_field_enabled_state_from_spec(spec)

    @staticmethod
    def _update_config_field_enabled_state_from_spec(spec: dict[str, object]) -> None:
        include_checkbox = spec["include_checkbox"]
        clear_checkbox = spec["clear_checkbox"]
        control = spec["control"]
        if spec.get("root_mode"):
            clear_checkbox.setEnabled(True)
            control.setEnabled(not clear_checkbox.isChecked())
        else:
            enabled = include_checkbox.isChecked()
            clear_checkbox.setEnabled(enabled)
            control.setEnabled(enabled and not clear_checkbox.isChecked())

    @staticmethod
    def _set_config_field_root_mode(spec: dict[str, object], *, is_root: bool) -> None:
        spec["root_mode"] = is_root
        spec["include_checkbox"].setVisible(not is_root)
        spec["inherited_label"].setVisible(not is_root)
        spec["clear_checkbox"].setText("Unset" if is_root else "Clear")
        if is_root:
            spec["clear_checkbox"].setEnabled(True)

    def _populate_config_form(self, domain: str, *, raw_config: dict[str, Any], inherited_config: dict[str, Any]) -> None:
        is_root = self._selected_directory_path == "root"
        self._config_form_loading = True
        try:
            field_specs = self._config_widgets[domain]["field_specs"]
            for field_name, spec in field_specs.items():
                self._set_config_field_root_mode(spec, is_root=is_root)
                if not is_root:
                    spec["inherited_label"].setText(
                        _format_config_value_preview(inherited_config.get(field_name))
                        if field_name in inherited_config
                        else "Inherited: not set"
                    )
                if is_root:
                    if field_name in raw_config and raw_config[field_name] is not None:
                        spec["include_checkbox"].setChecked(True)
                        spec["clear_checkbox"].setChecked(False)
                        self._update_config_field_control_value(spec, raw_config[field_name])
                    elif _root_config_field_starts_editable(domain, field_name, raw_config):
                        spec["include_checkbox"].setChecked(True)
                        spec["clear_checkbox"].setChecked(False)
                        self._update_config_field_control_value(spec, "")
                    else:
                        spec["include_checkbox"].setChecked(False)
                        spec["clear_checkbox"].setChecked(True)
                        self._update_config_field_control_value(spec, None)
                else:
                    if field_name in raw_config:
                        spec["include_checkbox"].setChecked(True)
                        if raw_config[field_name] is None:
                            spec["clear_checkbox"].setChecked(True)
                            self._update_config_field_control_value(spec, None)
                        else:
                            spec["clear_checkbox"].setChecked(False)
                            self._update_config_field_control_value(spec, raw_config[field_name])
                    else:
                        spec["include_checkbox"].setChecked(False)
                        spec["clear_checkbox"].setChecked(False)
                        self._update_config_field_control_value(spec, None)
                self._update_config_field_enabled_state_from_spec(spec)
        finally:
            self._config_form_loading = False
        self._config_dirty_domains.discard(domain)

    @staticmethod
    def _update_config_field_control_value(spec: dict[str, object], value: Any) -> None:
        kind = spec["kind"]
        control = spec["control"]
        if kind == "bool":
            control.setCurrentText("true" if bool(value) else "false")
        elif kind == "int":
            control.setValue(int(value) if value is not None else 0)
        elif kind == "float":
            control.setValue(float(value) if value is not None else 0.0)
        elif kind == "literal":
            text_value = "" if value is None else str(value)
            index = control.findText(text_value)
            if index >= 0:
                control.setCurrentIndex(index)
            elif control.count() > 0:
                control.setCurrentIndex(0)
        elif kind == "text":
            control.setText("" if value is None else str(value))
        elif kind == "multiline_text":
            control.setPlainText("" if value is None else str(value))
            control.document().setModified(False)
        elif kind == "string_list":
            if value is None:
                control.setPlainText("")
            else:
                if isinstance(value, str):
                    candidate_values = [value]
                elif isinstance(value, (list, tuple)):
                    candidate_values = list(value)
                else:
                    candidate_values = [value]
                lines = [str(item).strip() for item in candidate_values if str(item).strip()]
                control.setPlainText("\n".join(lines))
            control.document().setModified(False)
        else:
            text = "" if value is None else json.dumps(value, indent=2, sort_keys=True)
            control.setPlainText(text)
            control.document().setModified(False)

    @staticmethod
    def _read_config_field_control_value(spec: dict[str, object]) -> Any:
        kind = spec["kind"]
        control = spec["control"]
        if kind == "bool":
            return control.currentText() == "true"
        if kind == "int":
            return int(control.value())
        if kind == "float":
            return float(control.value())
        if kind == "literal":
            return control.currentText()
        if kind == "text":
            return control.text().strip()
        if kind == "multiline_text":
            return control.toPlainText().strip()
        if kind == "string_list":
            return [line.strip() for line in control.toPlainText().splitlines() if line.strip()]
        raw_text = control.toPlainText().strip()
        if not raw_text:
            raise ValueError("structured values must be valid JSON")
        return json.loads(raw_text)

    @staticmethod
    def _config_field_has_override(spec: dict[str, object]) -> bool:
        kind = spec["kind"]
        control = spec["control"]
        if kind == "bool":
            return True
        if kind in {"int", "float"}:
            return True
        if kind == "literal":
            return bool(control.currentText().strip())
        if kind == "text":
            return bool(control.text().strip())
        if kind == "multiline_text":
            return bool(control.toPlainText().strip())
        if kind == "string_list":
            return bool([line for line in control.toPlainText().splitlines() if line.strip()])
        return bool(control.toPlainText().strip())

    def _collect_config_form_payload(self, domain: str) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        is_root = self._selected_directory_path == "root"
        for field_name, spec in self._config_widgets[domain]["field_specs"].items():
            if not spec["include_checkbox"].isChecked():
                continue
            if spec["clear_checkbox"].isChecked():
                if is_root:
                    continue
                if _config_field_kind(field_name)[0] == "string_list":
                    payload[field_name] = []
                    continue
                payload[field_name] = None
                continue
            payload[field_name] = self._read_config_field_control_value(spec)
        return payload

    def _apply_directory_feature_state(self) -> None:
        enabled = self.service.directory_scoped_config_enabled
        self._update_directory_action_bar()
        for widgets in self._config_widgets.values():
            for spec in widgets["field_specs"].values():
                if spec.get("root_mode"):
                    spec["include_checkbox"].setEnabled(False)
                else:
                    spec["include_checkbox"].setEnabled(enabled)
                if not enabled:
                    spec["clear_checkbox"].setEnabled(False)
                    spec["control"].setEnabled(False)
                else:
                    self._update_config_field_enabled_state_from_spec(spec)
            widgets["save_button"].setEnabled(enabled)
        if not enabled:
            self.config_status_label.setText(
                "Directory-scoped config is disabled; using root configuration defaults."
            )

    def choose_directory(self) -> None:
        tree_result = self.service.list_directory_tree()
        if not tree_result.ok or tree_result.data is None:
            QMessageBox.warning(self, "Directories", "Directory management is unavailable; using root.")
            self._set_selected_directory("root")
            return

        def flatten(node) -> list[str]:
            values = [node.directory.path]
            for child in node.children:
                values.extend(flatten(child))
            return values

        options = flatten(tree_result.data)
        selected, accepted = QInputDialog.getItem(
            self,
            "Select directory",
            "Directory",
            options,
            options.index(self._selected_directory_path) if self._selected_directory_path in options else 0,
            False,
        )
        if accepted and selected:
            self._set_selected_directory(selected)

    def create_subdirectory(self) -> None:
        if not self.service.directory_scoped_config_enabled:
            QMessageBox.information(
                self,
                "Create sub-directory",
                "Directory-scoped config is disabled; set enable_directory_scoped_config=true to enable edits.",
            )
            return
        name, accepted = QInputDialog.getText(
            self,
            "Create sub-directory",
            f"New directory name under {self._selected_directory_path}",
        )
        if not accepted or not name.strip():
            return
        result = self.service.create_directory_child(self._selected_directory_path, name.strip())
        if not result.ok:
            message = result.errors[0].message if result.errors else "Directory create failed."
            QMessageBox.warning(self, "Create sub-directory", message)
            self.append_log(f"Create sub-directory failed: {message}")
            return
        self._set_selected_directory(self._selected_directory_path, refresh=True)
        self.append_log(f"Created directory {result.data.details.directory.path}")

    def delete_selected_directory(self) -> None:
        if not self.service.directory_scoped_config_enabled:
            QMessageBox.information(
                self,
                "Delete directory",
                "Directory-scoped config is disabled; set enable_directory_scoped_config=true to enable edits.",
            )
            return
        if self._selected_directory_path == "root":
            QMessageBox.information(self, "Delete directory", "The root directory cannot be deleted.")
            return
        answer = QMessageBox.question(
            self,
            "Delete directory",
            f"Delete directory {self._selected_directory_path}? Only empty directories can be deleted here.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self.service.directory_admin.delete(self._selected_directory_path, recursive=False)
        if not result.ok:
            message = result.errors[0].message if result.errors else "Directory delete failed."
            QMessageBox.warning(self, "Delete directory", message)
            self.append_log(f"Delete directory failed: {message}")
            return
        deleted_path = self._selected_directory_path
        parent_path = _parent_directory_path(deleted_path) or "root"
        self._set_selected_directory(parent_path, refresh=True)
        self.append_log(f"Deleted directory {deleted_path}")

    def refresh_configuration(self) -> None:
        self._config_request_token += 1
        request_token = self._config_request_token

        def load_config_state():
            state: dict[str, object] = {"selected_directory": self._selected_directory_path}
            directory_result = self.service.get_directory(self._selected_directory_path)
            state["directory"] = directory_result
            for domain in DIRECTORY_CONFIG_DOMAIN_FILENAMES:
                state[f"raw:{domain}"] = self.service.read_directory_domain_config(self._selected_directory_path, domain)
                state[f"effective:{domain}"] = self.service.compute_directory_effective_config(
                    self._selected_directory_path,
                    domain,
                )
                parent_path = _parent_directory_path(self._selected_directory_path)
                state[f"parent:{domain}"] = (
                    self.service.compute_directory_effective_config(parent_path, domain) if parent_path else None
                )
            return state

        worker = _ActionWorker(load_config_state)
        self._active_workers.add(worker)
        worker.signals.finished.connect(
            lambda payload, worker=worker, request_token=request_token: self._handle_config_refresh_success(
                payload, worker, request_token
            )
        )
        worker.signals.failed.connect(
            lambda error, worker=worker, request_token=request_token: self._handle_config_refresh_failure(
                error, worker, request_token
            )
        )
        self.thread_pool.start(worker)

    def _handle_config_refresh_success(self, payload, worker: _ActionWorker, request_token: int) -> None:
        self._discard_worker(worker)
        if request_token != self._config_request_token:
            return
        selected_directory = payload["selected_directory"]
        preserved_local_edits = False
        self.config_status_label.setText(f"Configuration for {selected_directory}")
        for domain, widgets in self._config_widgets.items():
            raw_result = payload.get(f"raw:{domain}")
            effective_result = payload.get(f"effective:{domain}")
            parent_result = payload.get(f"parent:{domain}")
            raw_config = {}
            if getattr(raw_result, "ok", False) and raw_result.data is not None and raw_result.data.config is not None:
                raw_config = raw_result.data.config
            parent_effective_config = {}
            if parent_result and getattr(parent_result, "ok", False) and parent_result.data is not None:
                parent_effective_config = parent_result.data.resolution.effective_config
            if domain not in self._config_dirty_domains:
                self._populate_config_form(
                    domain,
                    raw_config=raw_config,
                    inherited_config=parent_effective_config,
                )
            else:
                preserved_local_edits = True
            if parent_result and getattr(parent_result, "ok", False) and parent_result.data is not None:
                widgets["latest_parent_defaults_payload"] = {
                    "directory_path": _parent_directory_path(selected_directory),
                    "resolution": parent_result.data.model_dump(mode="json"),
                }
            else:
                widgets["latest_parent_defaults_payload"] = {
                    "directory_path": None,
                    "note": (
                        "Root directory has no parent directory. It is the top of the directory config "
                        "chain for this domain."
                    ),
                }
            if getattr(effective_result, "ok", False) and effective_result.data is not None:
                widgets["latest_effective_payload"] = {
                    "resolution": effective_result.data.model_dump(mode="json"),
                    "validation_diagnostics": [
                        diag.model_dump(mode="json") for diag in effective_result.validation_diagnostics
                    ],
                }
                diagnostics = [diag.message for diag in effective_result.validation_diagnostics]
                widgets["validation_view"].setPlainText("\n".join(diagnostics) if diagnostics else "No validation errors.")
            else:
                error_messages = [error.message for error in getattr(effective_result, "errors", [])]
                widgets["latest_effective_payload"] = {
                    "errors": error_messages,
                }
                widgets["validation_view"].setPlainText("\n".join(error_messages) if error_messages else "Unavailable.")
        if preserved_local_edits:
            self.config_status_label.setText(
                f"Configuration for {selected_directory}. Local override edits were preserved."
            )

    def _handle_config_refresh_failure(self, error: str, worker: _ActionWorker, request_token: int) -> None:
        self._discard_worker(worker)
        if request_token != self._config_request_token:
            return
        self.config_status_label.setText(f"Configuration unavailable: {error}")

    def show_parent_defaults_dialog(self, domain: str) -> None:
        widgets = self._config_widgets[domain]
        payload = widgets.get("latest_parent_defaults_payload")
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Resolved Parent Defaults: {domain}")
        dialog.resize(900, 640)
        layout = QVBoxLayout(dialog)
        text_view = QTextEdit()
        text_view.setReadOnly(True)
        if payload is None:
            text_view.setPlainText("Resolved parent defaults have not been loaded yet.")
        else:
            text_view.setPlainText(json.dumps(payload, indent=2, sort_keys=True))
        layout.addWidget(text_view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        buttons.button(QDialogButtonBox.Close).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def show_effective_config_dialog(self, domain: str) -> None:
        widgets = self._config_widgets[domain]
        payload = widgets.get("latest_effective_payload")
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Effective Configuration: {domain}")
        dialog.resize(900, 640)
        layout = QVBoxLayout(dialog)
        text_view = QTextEdit()
        text_view.setReadOnly(True)
        if payload is None:
            text_view.setPlainText("Effective configuration has not been loaded yet.")
        else:
            text_view.setPlainText(json.dumps(payload, indent=2, sort_keys=True))
        layout.addWidget(text_view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        buttons.button(QDialogButtonBox.Close).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def save_config_domain(self, domain: str) -> None:
        if not self.service.directory_scoped_config_enabled:
            self.config_status_label.setText(
                "Directory-scoped config is disabled; save is unavailable until enable_directory_scoped_config=true."
            )
            return
        widgets = self._config_widgets[domain]
        try:
            payload = self._collect_config_form_payload(domain)
        except (json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(self, "Save configuration", f"Invalid configuration value: {exc}")
            return
        result = self.service.write_directory_domain_config(self._selected_directory_path, domain, payload)
        if not result.ok:
            message = result.errors[0].message if result.errors else "Save failed."
            widgets["validation_view"].setPlainText(message)
            self.config_status_label.setText(f"Save failed for {domain}: {message}")
            return
        self._config_dirty_domains.discard(domain)
        self.config_status_label.setText(f"Saved {domain} overrides for {self._selected_directory_path}")
        self.refresh_configuration()

    def _apply_session_refresh(self, session_summaries) -> None:
        selected_session_id = None
        current_session = self.selected_session_summary()
        if current_session is not None:
            selected_session_id = current_session.session_id
        active_sessions = [summary for summary in session_summaries if not summary.is_expired]
        expired_sessions = [summary for summary in session_summaries if summary.is_expired]

        self._active_session_summaries = active_sessions
        self._expired_session_summaries = expired_sessions
        self.active_session_table.blockSignals(True)
        self.expired_session_table.blockSignals(True)
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
        self.active_session_table.blockSignals(False)
        self.expired_session_table.blockSignals(False)
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
        worker_id = worker.worker_id
        self._worker_log_request_token += 1
        request_token = self._worker_log_request_token
        log_worker = _ActionWorker(self.service.get_background_worker_log_snapshot, worker_id, lines=200)
        self._active_workers.add(log_worker)
        log_worker.signals.finished.connect(
            lambda text, worker=log_worker, request_token=request_token, worker_id=worker_id: self._handle_worker_log_success(
                text, worker, request_token, worker_id
            )
        )
        log_worker.signals.failed.connect(
            lambda error, worker=log_worker, request_token=request_token: self._handle_worker_log_failure(
                error, worker, request_token
            )
        )
        self.thread_pool.start(log_worker)

    def _handle_worker_log_success(
        self, text: str, worker: _ActionWorker, request_token: int, worker_id: str
    ) -> None:
        self._discard_worker(worker)
        current_worker = self.selected_worker_summary()
        if request_token != self._worker_log_request_token:
            return
        if current_worker is None or current_worker.worker_id != worker_id:
            return
        _set_text_preserving_scroll(self.worker_log_view, text)

    def _handle_worker_log_failure(self, error: str, worker: _ActionWorker, request_token: int) -> None:
        self._discard_worker(worker)
        if request_token != self._worker_log_request_token:
            return
        _set_text_preserving_scroll(self.worker_log_view, f"Worker log unavailable: {error}")

    def _on_worker_selection_changed(self) -> None:
        self.update_worker_action_bar()
        self.refresh_selected_worker_log()
        self.refresh_selected_worker_global_settings()

    def update_worker_action_bar(self) -> None:
        self.update_worker_selection_hint()
        self._update_worker_global_settings_visibility()
        self._update_worker_splitter_layout()

    def _update_worker_global_settings_visibility(self) -> None:
        worker = self.selected_worker_summary()
        supported_worker = worker is not None and worker.worker_id in WORKER_GLOBAL_CONFIG_MODELS
        self.worker_global_settings_section.setVisible(supported_worker)
        self.worker_global_settings_save_button.setEnabled(
            supported_worker and not self._worker_global_config_form_loading
        )
        self.worker_global_settings_reload_button.setEnabled(
            supported_worker and not self._worker_global_config_form_loading
        )
        if worker is None:
            self.worker_global_settings_status_label.setText("Select a worker to edit global settings.")
        elif not supported_worker:
            self.worker_global_settings_status_label.setText(
                f"Global settings are unavailable for {worker.name}."
            )

    def refresh_selected_worker_global_settings(self) -> None:
        worker = self.selected_worker_summary()
        if worker is None:
            self._worker_global_config_worker_id = None
            self._populate_worker_global_settings_form({})
            self.worker_global_settings_status_label.setText("Select a worker to edit global settings.")
            return
        if worker.worker_id not in WORKER_GLOBAL_CONFIG_MODELS:
            self._worker_global_config_worker_id = None
            self._populate_worker_global_settings_form({})
            self.worker_global_settings_status_label.setText(
                f"Global settings are unavailable for {worker.name}."
            )
            return
        self._set_worker_global_settings_page(worker.worker_id)
        self._worker_global_config_request_token += 1
        request_token = self._worker_global_config_request_token
        self._worker_global_config_worker_id = worker.worker_id
        load_worker = _ActionWorker(self.service.read_worker_global_config, worker.worker_id)
        self._active_workers.add(load_worker)
        load_worker.signals.finished.connect(
            lambda payload, worker=load_worker, request_token=request_token, worker_id=worker.worker_id: self._handle_worker_global_settings_success(
                payload,
                worker,
                request_token,
                worker_id,
            )
        )
        load_worker.signals.failed.connect(
            lambda error, worker=load_worker, request_token=request_token: self._handle_worker_global_settings_failure(
                error,
                worker,
                request_token,
            )
        )
        self.worker_global_settings_status_label.setText(f"Loading {worker.name} global settings...")
        self.thread_pool.start(load_worker)

    def _handle_worker_global_settings_success(
        self,
        payload,
        worker: _ActionWorker,
        request_token: int,
        worker_id: str,
    ) -> None:
        self._discard_worker(worker)
        if request_token != self._worker_global_config_request_token:
            return
        current_worker = self.selected_worker_summary()
        if current_worker is None or current_worker.worker_id != worker_id:
            return
        config = payload.config if isinstance(getattr(payload, "config", None), dict) else {}
        self._set_worker_global_settings_page(worker_id)
        self._populate_worker_global_settings_form(config)
        worker_name = current_worker.name if current_worker is not None else worker_id
        status_prefix = "Loaded existing config from" if getattr(payload, "exists", False) else "Using current settings from"
        self.worker_global_settings_status_label.setText(f"{status_prefix} {payload.path} for {worker_name}.")

    def _handle_worker_global_settings_failure(self, error: str, worker: _ActionWorker, request_token: int) -> None:
        self._discard_worker(worker)
        if request_token != self._worker_global_config_request_token:
            return
        self.worker_global_settings_status_label.setText(f"Worker global settings unavailable: {error}")

    def save_worker_global_settings(self) -> None:
        worker = self.selected_worker_summary()
        if worker is None or worker.worker_id not in WORKER_GLOBAL_CONFIG_MODELS:
            return
        try:
            payload = self._collect_worker_global_settings_payload()
        except (json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(self, "Save worker settings", f"Invalid worker setting value: {exc}")
            return
        try:
            self.service.write_worker_global_config(worker.worker_id, payload)
        except Exception as exc:  # pragma: no cover - GUI-only path
            message = str(exc)
            self.worker_global_settings_status_label.setText(f"Worker global settings save failed: {message}")
            QMessageBox.warning(self, "Save worker settings", message)
            return
        self._worker_global_config_dirty = False
        self.refresh_selected_worker_global_settings()

    def _build_worker_global_settings_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.worker_global_settings_status_label)
        layout.addWidget(self.worker_global_settings_form_scroll)
        layout.addLayout(self.worker_global_settings_button_row)
        return panel

    def _build_worker_global_settings_page(
        self,
        worker_id: str,
        model: type[BaseModel],
    ) -> tuple[QWidget, dict[str, dict[str, object]]]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        widgets: dict[str, dict[str, object]] = {}
        for field_name in sorted(model.model_fields):
            editor = self._build_worker_global_config_field_editor(field_name)
            label = QLabel(_pretty_config_field_name(field_name))
            tooltip = _worker_global_config_tooltip(field_name)
            if tooltip:
                label.setToolTip(tooltip)
                editor["container"].setToolTip(tooltip)
            form.addRow(label, editor["container"])
            widgets[field_name] = editor
        layout.addLayout(form)
        return page, widgets

    def _set_worker_global_settings_page(self, worker_id: str) -> None:
        page = self._worker_global_config_pages.get(worker_id)
        if page is None:
            return
        self.worker_global_settings_form_stack.setCurrentWidget(page)
        self._worker_global_config_worker_id = worker_id

    def _build_worker_global_config_field_editor(self, field_name: str) -> dict[str, object]:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        kind, literal_choices = _config_field_kind(field_name)
        if kind == "bool":
            control = QComboBox()
            control.addItem("false")
            control.addItem("true")
            control.setMaximumWidth(90)
        elif kind == "int":
            control = QSpinBox()
            control.setRange(-1_000_000_000, 1_000_000_000)
            control.setMaximumWidth(160)
        elif kind == "float":
            control = QDoubleSpinBox()
            control.setRange(-1_000_000_000.0, 1_000_000_000.0)
            control.setDecimals(6)
            control.setMaximumWidth(180)
        elif kind == "literal":
            control = QComboBox()
            for choice in literal_choices:
                control.addItem(str(choice))
            control.setMaximumWidth(260)
        elif kind == "text":
            control = QLineEdit()
            control.setMaximumWidth(520)
        elif kind == "multiline_text":
            control = QPlainTextEdit()
            control.setPlaceholderText("Text value")
            control.setFixedHeight(96)
        elif kind == "string_list":
            control = QPlainTextEdit()
            control.setPlaceholderText("One item per line")
            control.setFixedHeight(96)
        else:
            control = QPlainTextEdit()
            control.setPlaceholderText("JSON value")
            control.setFixedHeight(96)
        layout.addWidget(control)
        spec = {
            "container": container,
            "kind": kind,
            "control": control,
            "field_name": field_name,
        }
        self._update_config_field_control_value(spec, None)
        if kind == "bool":
            control.currentTextChanged.connect(lambda _value, field_name=field_name: self._mark_worker_global_settings_dirty())
        elif kind in {"int", "float"}:
            control.valueChanged.connect(lambda _value, field_name=field_name: self._mark_worker_global_settings_dirty())
        elif kind == "literal":
            control.currentTextChanged.connect(lambda _value, field_name=field_name: self._mark_worker_global_settings_dirty())
        elif kind == "text":
            control.textChanged.connect(lambda _value, field_name=field_name: self._mark_worker_global_settings_dirty())
        elif kind == "multiline_text":
            control.textChanged.connect(lambda field_name=field_name: self._mark_worker_global_settings_dirty())
        elif kind == "string_list":
            control.textChanged.connect(lambda field_name=field_name: self._mark_worker_global_settings_dirty())
        else:
            control.textChanged.connect(lambda field_name=field_name: self._mark_worker_global_settings_dirty())
        return spec

    def _mark_worker_global_settings_dirty(self) -> None:
        if self._worker_global_config_form_loading:
            return
        self._worker_global_config_dirty = True

    def _populate_worker_global_settings_form(self, payload: dict[str, Any]) -> None:
        self._worker_global_config_form_loading = True
        try:
            worker_id = self._worker_global_config_worker_id
            widgets = self._worker_global_config_widgets.get(worker_id or "", {})
            for field_name, spec in widgets.items():
                self._update_config_field_control_value(spec, payload.get(field_name))
        finally:
            self._worker_global_config_form_loading = False
        self._worker_global_config_dirty = False

    def _collect_worker_global_settings_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        worker_id = self._worker_global_config_worker_id
        widgets = self._worker_global_config_widgets.get(worker_id or "", {})
        for field_name, spec in widgets.items():
            value = self._read_config_field_control_value(spec)
            if field_name in {"yaml_output_dir", "setup_log_dir", "processed_store_path", "setup_lock_path"} and value == "":
                value = None
            if field_name in {
                "host_heartbeat_lock_path",
                "host_heartbeat_session_cooldown_path",
                "host_heartbeat_wake_state_path",
            } and value == "":
                value = None
            if field_name in {"runtime_dir", "setup_script_path"} and value == "":
                raise ValueError(f"{field_name} cannot be empty")
            if field_name in {"host_busy_check_script_path", "host_resume_script_path"} and value == "":
                raise ValueError(f"{field_name} cannot be empty")
            payload[field_name] = value
        return payload

    def _update_worker_splitter_layout(self) -> None:
        visible_sections = [section for section in (self.worker_log_section, self.worker_global_settings_section) if section.isVisible()]
        if any(section.is_expanded() for section in visible_sections):
            self.worker_details_panel.setMinimumHeight(0)
            self.worker_details_panel.setMaximumHeight(16777215)
            self.worker_splitter.setSizes([1, 1])
            return
        details_layout = self.worker_details_panel.layout()
        if details_layout is None:
            self.worker_details_panel.setMinimumHeight(0)
            self.worker_details_panel.setMaximumHeight(16777215)
            self.worker_splitter.setSizes([1, 1])
            return
        collapsed_height = sum(section.collapsed_height() for section in visible_sections)
        collapsed_height += details_layout.contentsMargins().top() + details_layout.contentsMargins().bottom()
        collapsed_height += max(details_layout.spacing(), 0) * max(len(visible_sections) - 1, 0)
        self.worker_details_panel.setMinimumHeight(collapsed_height)
        self.worker_details_panel.setMaximumHeight(collapsed_height)
        self.worker_splitter.setSizes([1000, collapsed_height])

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

    runtime_dir = ADMIN_APP_DEFAULT_RUNTIME_DIR
    argv = sys.argv[:]
    if len(argv) > 2 and argv[1] in {"--runtime-dir", "--config"}:
        runtime_dir = Path(argv[2])
        del argv[1:3]

    app = QApplication(argv)
    service = AdminService(runtime_dir)
    window = MainWindow(service)
    window.show()
    raise SystemExit(app.exec())
