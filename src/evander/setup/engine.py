from __future__ import annotations

import getpass
import json
import os
import secrets
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from evander.admin.composition import build_admin_runtime
from evander.admin.web.frontend_assets import (
    admin_frontend_assets_ready,
    admin_frontend_dist_dir,
    missing_admin_frontend_assets,
)
from evander.bootstrap import load_runtime_gateway_config
from evander.runtime import build_runtime

UTC = timezone.utc

SETUP_STATE_VERSION = 1
DEFAULT_SECRET_FILE = Path("/etc/evander/local-secrets.env")
DEFAULT_RUNTIME_DIR = Path("runtime")
DEFAULT_ADMIN_PORT = "8100"
DEFAULT_ADMIN_HOST = "127.0.0.1"
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_AUTH_MODE = "local_password"
DEFAULT_CONTAINER_WORKER_HOST = "127.0.0.1"
DEFAULT_CONTAINER_WORKER_PORT = "11890"
DEFAULT_CONTAINER_WORKER_URL = (
    f"http://{DEFAULT_CONTAINER_WORKER_HOST}:{DEFAULT_CONTAINER_WORKER_PORT}"
)
DEFAULT_CONTAINER_WORKER_TOKEN_ENV = "EVANDER_WORKER_TOKEN"
DEFAULT_CONTAINER_WORKER_RUNNER_IMAGE = "evander-runner:latest"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11435/v1"
LOCAL_CONTAINER_WORKER_SERVICE = "evander-worker-daemon.service"
LOCAL_CONTAINER_WORKER_ROOT = Path("/var/lib/agent-worker")
LOCAL_CONTAINER_WORKER_RUNTIME_DIR = LOCAL_CONTAINER_WORKER_ROOT / "runtime"
LOCAL_CONTAINER_WORKER_TOOLCHAIN_CACHE_ROOT = LOCAL_CONTAINER_WORKER_ROOT / "toolchain-cache"
LOCAL_CONTAINER_WORKER_GIT_KEY_NAME = "evander-git"
LOCAL_CONTAINER_WORKER_GIT_SSH_HOST = "127.0.0.1"
LOCAL_CONTAINER_WORKER_GIT_ALLOWED_FROM = "127.0.0.1"
LOCAL_CONTAINER_WORKER_READONLY_MOUNTS = (
    (
        LOCAL_CONTAINER_WORKER_TOOLCHAIN_CACHE_ROOT / "dotnet",
        "/opt/evander/toolchains/dotnet",
    ),
    (
        LOCAL_CONTAINER_WORKER_TOOLCHAIN_CACHE_ROOT / "dotnet-tools",
        "/opt/evander/toolchains/dotnet-tools",
    ),
    (
        LOCAL_CONTAINER_WORKER_TOOLCHAIN_CACHE_ROOT / "node-v24.14.0",
        "/opt/evander/toolchains/node-v24.14.0",
    ),
    (
        LOCAL_CONTAINER_WORKER_TOOLCHAIN_CACHE_ROOT / "playwright-runtime",
        "/opt/evander/playwright-runtime",
    ),
    (
        LOCAL_CONTAINER_WORKER_TOOLCHAIN_CACHE_ROOT / "ms-playwright",
        "/opt/evander/ms-playwright",
    ),
    (
        LOCAL_CONTAINER_WORKER_TOOLCHAIN_CACHE_ROOT / "nuget-cache-ro",
        "/opt/evander/nuget-cache-ro",
    ),
)

PROVIDER_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

PROVIDER_DEFAULT_MODELS = {
    "openai": "gpt-5.4",
    "anthropic": "claude-sonnet-4-6",
    "ollama": "qwen2.5-coder:14b",
}

ESSENTIAL_SERVICES = [
    "evander-firewall.service",
    "evander-gateway.service",
    "evander-admin-web.service",
    "evander-openai-relay.service",
    "evander-nuget-relay.service",
    "evander-http-target-relay.service",
    "evander-managed-git-refresh.service",
    "evander-trigger-orchestrator.service",
    "evander-effect-orchestrator.service",
]

SETUP_SERVICE_PROFILE = "setup"


@dataclass
class SetupState:
    version: int = SETUP_STATE_VERSION
    secret_file: str = str(DEFAULT_SECRET_FILE)
    secret_storage_acknowledged: bool = False
    admin_web: dict[str, Any] = field(
        default_factory=lambda: {
            "host": DEFAULT_ADMIN_HOST,
            "port": DEFAULT_ADMIN_PORT,
            "auth_mode": DEFAULT_ADMIN_AUTH_MODE,
            "username": getpass.getuser() or DEFAULT_ADMIN_USERNAME,
            "password_configured": False,
            "session_secret_configured": False,
        }
    )
    execution: dict[str, Any] = field(default_factory=dict)
    ai_providers: list[str] = field(default_factory=list)
    provider_config: dict[str, Any] = field(default_factory=dict)
    provider_secrets_configured: dict[str, bool] = field(default_factory=dict)
    default_provider: str = ""
    default_model: str = ""
    install_preflight_passed_at: str = ""
    launched_at: str = ""
    assistant_verified_at: str = ""
    assistant_verification_error: str = ""
    assistant_session_id: str = ""
    assistant_smoke_event_id: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SetupState":
        known = {field.name for field in cls.__dataclass_fields__.values()}
        kwargs = {key: value for key, value in payload.items() if key in known}
        state = cls(**kwargs)
        state.version = SETUP_STATE_VERSION
        return state

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["admin_web"] = _redact_secretish_mapping(payload.get("admin_web"))
        return payload


@dataclass(frozen=True)
class ChecklistItem:
    id: str
    label: str
    status: str
    summary: str
    required: bool = True
    details: str = ""

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


class SetupPreflightError(RuntimeError):
    def __init__(self, *, returncode: int) -> None:
        super().__init__(f"install preflight failed with exit code {returncode}")
        self.returncode = returncode


class SetupDependencyInstallRejected(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _redact_secretish_mapping(value: object) -> object:
    if not isinstance(value, dict):
        return value
    redacted: dict[str, object] = {}
    for key, item in value.items():
        if "password" in str(key).lower() or "secret" in str(key).lower():
            redacted[key] = bool(item)
        else:
            redacted[key] = item
    return redacted


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_env_file(path: Path) -> dict[str, str]:
    try:
        if not path.is_file():
            return {}
    except PermissionError:
        return {}
    return _parse_env_content(path.read_text(encoding="utf-8"))


def _parse_env_content(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _quote_env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _read_env_for_update(path: Path, *, root_owned: bool) -> dict[str, str]:
    if not root_owned or os.geteuid() == 0:
        return _parse_env_file(path)
    result = subprocess.run(
        ["sudo", "cat", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return _parse_env_content(result.stdout)
    if (
        "No such file" in result.stderr
        or "cannot open" in result.stderr
        or "a terminal is required" in result.stderr
        or "a password is required" in result.stderr
    ):
        return {}
    result.check_returncode()
    return {}


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except PermissionError:
        return False


def _upsert_env_file(path: Path, updates: dict[str, str], *, root_owned: bool = False) -> None:
    existing = _read_env_for_update(path, root_owned=root_owned)
    existing.update(updates)
    content = "".join(
        f"{key}={_quote_env_value(value)}\n" for key, value in sorted(existing.items())
    )
    if root_owned and os.geteuid() != 0:
        tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"evander-secrets-{secrets.token_hex(8)}.env"
        tmp.write_text(content, encoding="utf-8")
        tmp.chmod(0o600)
        try:
            subprocess.run(
                [
                    "sudo",
                    "install",
                    "-d",
                    "-o",
                    "root",
                    "-g",
                    "root",
                    "-m",
                    "0700",
                    str(path.parent),
                ],
                check=True,
            )
            subprocess.run(
                ["sudo", "install", "-o", "root", "-g", "root", "-m", "0600", str(tmp), str(path)],
                check=True,
            )
            _grant_secret_file_git_access(path, _setup_git_user())
        finally:
            tmp.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    if root_owned:
        _grant_secret_file_git_access(path, _setup_git_user())


def _grant_secret_file_git_access(path: Path, git_user: str) -> None:
    if not git_user:
        return
    path_text = str(path)
    if not path.is_absolute() or not path_text.startswith("/etc/"):
        return
    if not _sudo_path_exists(path):
        return
    if shutil.which("setfacl"):
        subprocess.run(["sudo", "setfacl", "-m", f"u:{git_user}:--x", str(path.parent)], check=True)
        subprocess.run(["sudo", "setfacl", "-m", f"u:{git_user}:r--", str(path)], check=True)
        return
    git_group = _primary_group_for_user(git_user)
    subprocess.run(["sudo", "chgrp", git_group, str(path.parent), str(path)], check=True)
    subprocess.run(["sudo", "chmod", "0710", str(path.parent)], check=True)
    subprocess.run(["sudo", "chmod", "0640", str(path)], check=True)


def _sudo_path_exists(path: Path) -> bool:
    try:
        if path.exists():
            return True
    except PermissionError:
        pass
    result = subprocess.run(["sudo", "test", "-e", str(path)], check=False)
    return result.returncode == 0


def _copy_if_missing(source: Path, destination: Path) -> bool:
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def _merge_env_file(path: Path, updates: dict[str, str]) -> None:
    existing = _parse_env_file(path)
    existing.update(updates)
    content = "".join(
        f"{key}={_quote_env_value(value)}\n" for key, value in sorted(existing.items())
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _command_status(command: str) -> dict[str, object]:
    path = shutil.which(command)
    return {"command": command, "present": bool(path), "path": path}


class SetupManager:
    def __init__(self, *, project_dir: Path, runtime_dir: Path = DEFAULT_RUNTIME_DIR) -> None:
        self.project_dir = project_dir.expanduser().resolve()
        self.runtime_dir = (
            runtime_dir
            if runtime_dir.is_absolute()
            else (self.project_dir / runtime_dir).expanduser().resolve()
        )
        self.state_path = self.runtime_dir / "setup-wizard" / "state.json"

    def load_state(self) -> SetupState:
        return SetupState.from_payload(_read_json(self.state_path))

    def save_state(self, state: SetupState) -> None:
        _write_json(self.state_path, asdict(state))

    def secret_file_path(self, state: SetupState) -> Path:
        return Path(state.secret_file).expanduser()

    def _secret_file_requires_root(self, state: SetupState) -> bool:
        path = self.secret_file_path(state)
        return path.is_absolute() and str(path).startswith("/etc/")

    def secret_status(self, state: SetupState) -> dict[str, object]:
        path = self.secret_file_path(state)
        values = _read_env_for_update(path, root_owned=self._secret_file_requires_root(state))
        selected = set(state.ai_providers)
        required = {
            provider: PROVIDER_ENV_VARS[provider]
            for provider in selected
            if provider in PROVIDER_ENV_VARS
        }
        return {
            "path": str(path),
            "exists": _is_file(path),
            "required": {
                provider: {
                    "env_var": env_var,
                    "set": bool(
                        values.get(env_var) or state.provider_secrets_configured.get(provider)
                    ),
                }
                for provider, env_var in required.items()
            },
        }

    def checklist(self, state: SetupState | None = None) -> list[ChecklistItem]:
        state = state or self.load_state()
        secret_status = self.secret_status(state)
        items: list[ChecklistItem] = []
        items.append(
            ChecklistItem(
                "secret_storage",
                "Host secret storage",
                "completed" if state.secret_storage_acknowledged else "needs input",
                state.secret_file,
                details="Provider secrets are stored on the control-plane host.",
            )
        )
        admin_auth_mode = str(state.admin_web.get("auth_mode") or DEFAULT_ADMIN_AUTH_MODE)
        admin_session_ready = bool(state.admin_web.get("session_secret_configured"))
        admin_ready = admin_session_ready and (
            admin_auth_mode == "none" or bool(state.admin_web.get("password_configured"))
        )
        admin_summary = (
            f"{state.admin_web.get('host')}:{state.admin_web.get('port')}, "
            f"{_admin_auth_label(admin_auth_mode)}"
        )
        items.append(
            ChecklistItem(
                "admin_web",
                "Admin Web access",
                "completed" if admin_ready else "needs input",
                admin_summary,
            )
        )
        execution_model = str(state.execution.get("model") or "")
        items.append(
            ChecklistItem(
                "execution_model",
                "Execution model",
                "completed" if execution_model else "needs input",
                _execution_label(execution_model) if execution_model else "Not selected",
            )
        )
        provider_missing = [
            provider
            for provider, payload in dict(secret_status.get("required") or {}).items()
            if not payload.get("set")
        ]
        provider_status = "needs input"
        provider_summary = "Not selected"
        if state.ai_providers and not provider_missing:
            provider_status = "completed"
            provider_summary = ", ".join(state.ai_providers)
        elif state.ai_providers:
            provider_summary = (
                f"{', '.join(state.ai_providers)}; missing secrets: {', '.join(provider_missing)}"
            )
        items.append(
            ChecklistItem(
                "ai_providers",
                "AI providers",
                provider_status,
                provider_summary,
            )
        )
        default_ready = bool(state.default_provider and state.default_model)
        default_blocked = not state.ai_providers
        items.append(
            ChecklistItem(
                "default_model",
                "Default model routing",
                "blocked" if default_blocked else ("completed" if default_ready else "needs input"),
                (
                    f"{state.default_provider}: {state.default_model}"
                    if default_ready
                    else "Needs AI provider first"
                    if default_blocked
                    else "Not selected"
                ),
            )
        )
        required_ready = all(
            item.status == "completed"
            for item in items
            if item.required and item.id not in {"install_preflight", "launch_verify"}
        )
        items.append(
            ChecklistItem(
                "install_preflight",
                "Install preflight",
                (
                    "completed"
                    if state.install_preflight_passed_at
                    else "blocked"
                    if not required_ready
                    else "needs input"
                ),
                (
                    f"Passed {state.install_preflight_passed_at}"
                    if state.install_preflight_passed_at
                    else "Waiting for required choices"
                    if not required_ready
                    else "Ready to run"
                ),
            )
        )
        launch_ready = required_ready and bool(state.install_preflight_passed_at)
        items.append(
            ChecklistItem(
                "launch_verify",
                "Launch and Admin Assistant verification",
                "completed"
                if state.assistant_verified_at
                else "blocked"
                if not launch_ready
                else "needs input",
                (
                    f"Verified {state.assistant_verified_at}"
                    if state.assistant_verified_at
                    else "Ready to launch"
                    if launch_ready
                    else "Needs completed required items and preflight"
                ),
            )
        )
        return items

    def required_ready(self, state: SetupState | None = None) -> bool:
        state = state or self.load_state()
        return all(
            item.status == "completed"
            for item in self.checklist(state)
            if item.required and item.id not in {"launch_verify"}
        )

    def configuration_ready(self, state: SetupState | None = None) -> bool:
        state = state or self.load_state()
        required_config_ids = {
            "secret_storage",
            "admin_web",
            "execution_model",
            "ai_providers",
            "default_model",
        }
        return all(
            item.status == "completed"
            for item in self.checklist(state)
            if item.id in required_config_ids
        )

    def doctor(self) -> dict[str, object]:
        state = self.load_state()
        commands = [
            _command_status(command)
            for command in ("python3", "uv", "virsh", "virt-install", "cloud-localds", "qemu-img")
        ]
        paths = {
            "project_dir": str(self.project_dir),
            "runtime_dir": str(self.runtime_dir),
            "state_path": str(self.state_path),
            "admin_web_env": str(self.project_dir / "config" / "admin-web.env"),
            "firewall_env": str(self.project_dir / "config" / "firewall.env"),
        }
        return {
            "ok": self.required_ready(state),
            "checklist": [item.to_payload() for item in self.checklist(state)],
            "commands": commands,
            "paths": paths,
            "secret_status": self.secret_status(state),
        }

    def plan(self) -> dict[str, object]:
        state = self.load_state()
        files = [
            "runtime/setup-wizard/state.json",
            "runtime/host-config.json",
            "runtime/directory-config/root/providers.json",
            "runtime/directory-config/root/issue-setup.json",
            "runtime/assistant-settings.json",
            "config/admin-web.env",
            "config/firewall.env",
            state.secret_file,
        ]
        steps = [
            "initialize runtime state",
            "write Admin Web and firewall config",
            "write root provider/default-model config",
            "run systemd install preflight",
            "install/start persistent control-plane services",
            "create or reuse the Admin Assistant session",
            "queue one Admin Assistant smoke message",
        ]
        return {
            "ready": self.required_ready(state),
            "execution_model": state.execution.get("model") or None,
            "ai_providers": list(state.ai_providers),
            "default_provider": state.default_provider or None,
            "default_model": state.default_model or None,
            "files": files,
            "steps": steps,
            "secrets": self.secret_status(state),
            "checklist": [item.to_payload() for item in self.checklist(state)],
        }

    def acknowledge_secret_storage(self, state: SetupState) -> SetupState:
        state.secret_storage_acknowledged = True
        self.save_state(state)
        return state

    def configure_admin_web(
        self,
        state: SetupState,
        *,
        host: str,
        port: str,
        username: str,
        password: str = "",
        auth_mode: str = DEFAULT_ADMIN_AUTH_MODE,
    ) -> SetupState:
        normalized_auth_mode = auth_mode.strip().lower() or DEFAULT_ADMIN_AUTH_MODE
        if normalized_auth_mode not in {"local_password", "none"}:
            raise ValueError("Setup currently supports local_password and none Admin Web auth.")
        state.admin_web = {
            "host": host.strip() or DEFAULT_ADMIN_HOST,
            "port": port.strip() or DEFAULT_ADMIN_PORT,
            "auth_mode": normalized_auth_mode,
            "username": username.strip() or DEFAULT_ADMIN_USERNAME,
            "password_configured": normalized_auth_mode == "local_password" and bool(password),
            "session_secret_configured": True,
        }
        secret_updates = {
            "EVANDER_ADMIN_WEB_SESSION_SECRET": secrets.token_urlsafe(32),
        }
        if normalized_auth_mode == "local_password" and password:
            secret_updates["EVANDER_ADMIN_WEB_PASSWORD"] = password
        if secret_updates:
            _upsert_env_file(
                self.secret_file_path(state),
                secret_updates,
                root_owned=self._secret_file_requires_root(state),
            )
        self.save_state(state)
        return state

    def configure_execution(self, state: SetupState, *, model: str) -> SetupState:
        normalized = model.strip()
        if normalized not in {"container_same_host", "container_worker_host", "libvirt_vm"}:
            raise ValueError("Unsupported execution model")
        state.execution = {"model": normalized}
        if normalized == "container_same_host":
            state.execution.update(
                {
                    "execution_backend": "container_worker_vm",
                    "container_worker_base_url": DEFAULT_CONTAINER_WORKER_URL,
                    "container_worker_token_env": DEFAULT_CONTAINER_WORKER_TOKEN_ENV,
                }
            )
        elif normalized == "container_worker_host":
            state.execution.update(
                {
                    "execution_backend": "container_worker_vm",
                    "container_worker_token_env": DEFAULT_CONTAINER_WORKER_TOKEN_ENV,
                }
            )
        else:
            state.execution.update({"execution_backend": "libvirt_vm"})
        self.save_state(state)
        return state

    def configure_ai_providers(
        self,
        state: SetupState,
        *,
        providers: list[str],
        secrets_by_env: dict[str, str] | None = None,
        ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL,
    ) -> SetupState:
        normalized = [
            provider for provider in providers if provider in {"openai", "anthropic", "ollama"}
        ]
        if not normalized:
            raise ValueError("At least one AI provider is required")
        state.ai_providers = list(dict.fromkeys(normalized))
        config = dict(state.provider_config)
        if "openai" in normalized:
            config["openai_api_key_env"] = PROVIDER_ENV_VARS["openai"]
        if "anthropic" in normalized:
            config["anthropic_api_key_env"] = PROVIDER_ENV_VARS["anthropic"]
        if "ollama" in normalized:
            config["local_openai_api_base_url"] = ollama_base_url.strip() or DEFAULT_OLLAMA_BASE_URL
            config["local_openai_api_key"] = "ollama"
            config["local_openai_provider_name"] = "local_openai"
        secrets_by_env = secrets_by_env or {}
        secret_updates = {key: value for key, value in secrets_by_env.items() if value}
        if secret_updates:
            _upsert_env_file(
                self.secret_file_path(state),
                secret_updates,
                root_owned=self._secret_file_requires_root(state),
            )
        state.provider_config = config
        existing_values = _parse_env_file(self.secret_file_path(state))
        state.provider_secrets_configured = {
            provider: bool(
                existing_values.get(env_var)
                or secret_updates.get(env_var)
                or state.provider_secrets_configured.get(provider)
            )
            for provider, env_var in PROVIDER_ENV_VARS.items()
            if provider in state.ai_providers
        }
        if not state.default_provider or state.default_provider not in state.ai_providers:
            state.default_provider = state.ai_providers[0]
            state.default_model = PROVIDER_DEFAULT_MODELS[state.default_provider]
        self.save_state(state)
        return state

    def configure_default_model(
        self,
        state: SetupState,
        *,
        provider: str,
        model: str,
    ) -> SetupState:
        if provider not in state.ai_providers:
            raise ValueError("Default provider must be one of the selected AI providers")
        if not model.strip():
            raise ValueError("Default model is required")
        state.default_provider = provider
        state.default_model = model.strip()
        self.save_state(state)
        return state

    def run_install_preflight(self, state: SetupState) -> SetupState:
        self._write_config_files(state)
        completed = subprocess.run(
            [str(self.project_dir / "scripts" / "install_systemd_units.sh"), "--preflight"],
            cwd=self.project_dir,
            env={**os.environ, **_setup_install_env(state)},
            check=False,
        )
        if completed.returncode != 0:
            state.install_preflight_passed_at = ""
            self.save_state(state)
            raise SetupPreflightError(returncode=completed.returncode)
        state.install_preflight_passed_at = _now()
        self.save_state(state)
        return state

    def launch(
        self,
        state: SetupState,
        *,
        approve_nodejs_npm_install: Callable[[dict[str, str]], bool] | None = None,
    ) -> SetupState:
        if not self.required_ready(state):
            raise ValueError("Setup checklist is not ready to launch")
        self._write_config_files(state)
        self._ensure_admin_frontend_assets(approve_nodejs_npm_install=approve_nodejs_npm_install)
        subprocess.run(
            [
                "sudo",
                "env",
                *[f"{key}={value}" for key, value in _setup_install_env(state).items()],
                str(self.project_dir / "scripts" / "install_systemd_units.sh"),
            ],
            cwd=self.project_dir,
            check=True,
        )
        if _same_host_container_worker_selected(state):
            self._install_local_container_worker_daemon(state)
        self._install_secret_dropins(state)
        if _same_host_container_worker_selected(state):
            subprocess.run(
                ["sudo", "systemctl", "restart", LOCAL_CONTAINER_WORKER_SERVICE],
                check=True,
            )
        for service in ESSENTIAL_SERVICES:
            subprocess.run(["sudo", "systemctl", "start", service], check=True)
        self._wait_admin_web(state)
        self._verify_admin_assistant(state)
        state.launched_at = state.launched_at or _now()
        state.assistant_verified_at = _now()
        state.assistant_verification_error = ""
        self.save_state(state)
        return state

    def _ensure_admin_frontend_assets(
        self,
        *,
        approve_nodejs_npm_install: Callable[[dict[str, str]], bool] | None = None,
    ) -> None:
        frontend_dir = self.project_dir / "frontend" / "workflow-editor"
        dist_dir = admin_frontend_dist_dir(self.project_dir)
        if admin_frontend_assets_ready(dist_dir):
            return

        package_json = frontend_dir / "package.json"
        if not package_json.is_file():
            raise RuntimeError(
                f"Admin Web frontend source is missing at {frontend_dir}; "
                "cannot build static assets."
            )
        npm_path = shutil.which("npm")
        if npm_path is None:
            self._install_nodejs_npm_for_admin_frontend(
                dist_dir,
                approve_nodejs_npm_install=approve_nodejs_npm_install,
            )
            npm_path = shutil.which("npm")
            if npm_path is None:
                raise RuntimeError(
                    "Admin Web frontend assets are missing or stale, and npm was still "
                    "not available after installing Node.js/npm. Install Node.js/npm, "
                    "then rerun Launch and Admin Assistant verification."
                )

        if not (frontend_dir / "node_modules").is_dir():
            install_command = [
                npm_path,
                "ci" if (frontend_dir / "package-lock.json").is_file() else "install",
            ]
            subprocess.run(
                [*install_command, "--no-audit", "--no-fund"],
                cwd=frontend_dir,
                check=True,
            )
        subprocess.run([npm_path, "run", "build"], cwd=frontend_dir, check=True)

        missing_after_build = missing_admin_frontend_assets(dist_dir)
        if missing_after_build:
            missing = ", ".join(str(path) for path in missing_after_build)
            raise RuntimeError(
                f"Admin Web frontend build did not produce required assets: {missing}"
            )

    def _install_nodejs_npm_for_admin_frontend(
        self,
        dist_dir: Path,
        *,
        approve_nodejs_npm_install: Callable[[dict[str, str]], bool] | None,
    ) -> None:
        apt_get = shutil.which("apt-get")
        apt_cache = shutil.which("apt-cache")
        sudo = shutil.which("sudo")
        running_as_root = getattr(os, "geteuid", lambda: 1)() == 0
        if apt_get is None or apt_cache is None or (sudo is None and not running_as_root):
            missing = ", ".join(str(path) for path in missing_admin_frontend_assets(dist_dir))
            raise RuntimeError(
                "Admin Web frontend assets are missing or stale and npm is not installed. "
                "Install Node.js/npm, or run setup on an apt-based host with sudo and apt-cache available "
                "so guided setup can install them automatically. "
                f"Missing assets: {missing}"
            )

        versions = self._apt_candidate_versions(apt_cache, ["nodejs", "npm"])
        if approve_nodejs_npm_install is None:
            raise RuntimeError(
                "Admin Web frontend assets are missing or stale and npm is not installed. "
                f"Guided setup can install Node.js {versions['nodejs']} and npm {versions['npm']}; "
                "rerun setup interactively to approve the install, or install Node.js/npm manually."
            )
        if not approve_nodejs_npm_install(versions):
            raise SetupDependencyInstallRejected(
                "Node.js/npm installation was not approved; setup launch cannot continue."
            )

        print("Installing approved Node.js/npm packages with apt-get...")
        install_command = (
            [apt_get, "install", "-y", f"nodejs={versions['nodejs']}", f"npm={versions['npm']}"]
            if running_as_root
            else [
                sudo,
                "env",
                "DEBIAN_FRONTEND=noninteractive",
                apt_get,
                "install",
                "-y",
                f"nodejs={versions['nodejs']}",
                f"npm={versions['npm']}",
            ]
        )
        subprocess.run(install_command, check=True)

    def _apt_candidate_versions(self, apt_cache: str, packages: list[str]) -> dict[str, str]:
        versions = {}
        for package in packages:
            completed = subprocess.run(
                [apt_cache, "policy", package],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise RuntimeError(
                    f"Could not resolve apt candidate version for {package}: {detail}"
                )
            candidate = ""
            for raw_line in completed.stdout.splitlines():
                line = raw_line.strip()
                if line.startswith("Candidate:"):
                    candidate = line.split(":", 1)[1].strip()
                    break
            if not candidate or candidate == "(none)":
                raise RuntimeError(f"No apt candidate version is available for {package}.")
            versions[package] = candidate
        return versions

    def _write_config_files(self, state: SetupState) -> None:
        self._ensure_container_worker_secret(state)
        build_runtime(
            load_runtime_gateway_config(runtime_dir=self.runtime_dir)
        ).directories.ensure_root()
        _copy_if_missing(
            self.project_dir / "config" / "firewall.env.example",
            self.project_dir / "config" / "firewall.env",
        )
        admin_env = self.project_dir / "config" / "admin-web.env"
        _copy_if_missing(self.project_dir / "config" / "admin-web.env.example", admin_env)
        admin_updates = {
            "ADMIN_WEB_HOST": str(state.admin_web.get("host") or DEFAULT_ADMIN_HOST),
            "ADMIN_WEB_PORT": str(state.admin_web.get("port") or DEFAULT_ADMIN_PORT),
            "EVANDER_ADMIN_WEB_AUTH_MODE": str(
                state.admin_web.get("auth_mode") or DEFAULT_ADMIN_AUTH_MODE
            ),
            "EVANDER_ADMIN_WEB_USERNAME": str(
                state.admin_web.get("username") or DEFAULT_ADMIN_USERNAME
            ),
            "EVANDER_ADMIN_WEB_PASSWORD": "",
            "EVANDER_ADMIN_WEB_SESSION_SECRET": "",
            "EVANDER_ADMIN_WEB_COOKIE_SECURE": "false",
        }
        _merge_env_file(admin_env, admin_updates)
        provider_payload = self._provider_payload(state)
        issue_payload = {"default_model": state.default_model}
        admin_runtime = build_admin_runtime(self.runtime_dir)
        operations = admin_runtime.operations_admin
        operations.write_directory_domain_config("root", "providers", provider_payload)
        operations.write_directory_domain_config("root", "issue_setup", issue_payload)
        operations.write_assistant_settings(
            {
                "enabled": True,
                "read_only": True,
                "default_model": state.default_model,
                "show_launcher_when_disabled": False,
            }
        )

    def _ensure_container_worker_secret(self, state: SetupState) -> None:
        if str(state.execution.get("execution_backend") or "") != "container_worker_vm":
            return
        token_env = (
            str(state.execution.get("container_worker_token_env") or "").strip()
            or DEFAULT_CONTAINER_WORKER_TOKEN_ENV
        )
        secret_path = self.secret_file_path(state)
        root_owned = self._secret_file_requires_root(state)
        existing_values = _read_env_for_update(secret_path, root_owned=root_owned)
        token = (
            existing_values.get(token_env) or os.environ.get(token_env) or secrets.token_urlsafe(32)
        )
        if existing_values.get(token_env) != token:
            _upsert_env_file(secret_path, {token_env: token}, root_owned=root_owned)
        os.environ[token_env] = token

    def _install_secret_dropins(self, state: SetupState) -> None:
        secret_file = str(self.secret_file_path(state))
        _grant_secret_file_git_access(Path(secret_file), _setup_git_user())
        content = (
            "[Service]\n"
            f"Environment=EVANDER_SECRETS_ENV_FILE={secret_file}\n"
            f"EnvironmentFile=-{secret_file}\n"
        )
        tmp = (
            Path(os.environ.get("TMPDIR", "/tmp"))
            / f"evander-secret-dropin-{secrets.token_hex(8)}.conf"
        )
        tmp.write_text(content, encoding="utf-8")
        try:
            for service in ESSENTIAL_SERVICES:
                dropin_dir = f"/etc/systemd/system/{service}.d"
                subprocess.run(["sudo", "install", "-d", "-m", "0755", dropin_dir], check=True)
                subprocess.run(
                    [
                        "sudo",
                        "install",
                        "-m",
                        "0644",
                        str(tmp),
                        f"{dropin_dir}/20-evander-setup-secrets.conf",
                    ],
                    check=True,
                )
            subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        finally:
            tmp.unlink(missing_ok=True)

    def _install_local_container_worker_daemon(self, state: SetupState) -> None:
        self._ensure_container_worker_secret(state)
        run_user = _setup_run_user()
        run_group = _primary_group_for_user(run_user)
        git_ssh_alias = _local_worker_git_ssh_alias()
        git_ssh_source_dir = _local_worker_git_ssh_source_dir(run_user)
        self._ensure_local_container_worker_git_access(
            git_user=_setup_git_user(),
            git_ssh_alias=git_ssh_alias,
            git_ssh_source_dir=git_ssh_source_dir,
        )
        unit_content = self._local_container_worker_unit_content(state, run_user=run_user)
        tmp = (
            Path(os.environ.get("TMPDIR", "/tmp"))
            / f"evander-container-worker-{secrets.token_hex(8)}.service"
        )
        tmp.write_text(unit_content, encoding="utf-8")
        try:
            subprocess.run(
                [
                    "sudo",
                    "install",
                    "-d",
                    "-o",
                    run_user,
                    "-g",
                    run_group,
                    "-m",
                    "0750",
                    str(LOCAL_CONTAINER_WORKER_ROOT),
                    str(LOCAL_CONTAINER_WORKER_RUNTIME_DIR),
                    str(LOCAL_CONTAINER_WORKER_TOOLCHAIN_CACHE_ROOT),
                    *[
                        str(host_path)
                        for host_path, _container_path in LOCAL_CONTAINER_WORKER_READONLY_MOUNTS
                    ],
                ],
                check=True,
            )
            subprocess.run(
                [
                    "sudo",
                    "install",
                    "-m",
                    "0644",
                    str(tmp),
                    f"/etc/systemd/system/{LOCAL_CONTAINER_WORKER_SERVICE}",
                ],
                check=True,
            )
            subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
            subprocess.run(
                ["sudo", "systemctl", "enable", LOCAL_CONTAINER_WORKER_SERVICE],
                check=True,
            )
        finally:
            tmp.unlink(missing_ok=True)

    def _ensure_local_container_worker_git_access(
        self,
        *,
        git_user: str,
        git_ssh_alias: str,
        git_ssh_source_dir: Path,
    ) -> None:
        self._ensure_local_container_worker_git_key(
            git_ssh_source_dir,
            key_name=LOCAL_CONTAINER_WORKER_GIT_KEY_NAME,
        )
        self._write_local_container_worker_git_ssh_config(
            git_ssh_source_dir / "config",
            git_ssh_alias=git_ssh_alias,
            git_host=LOCAL_CONTAINER_WORKER_GIT_SSH_HOST,
            git_user=git_user,
            identity_file=f"~/.ssh/{LOCAL_CONTAINER_WORKER_GIT_KEY_NAME}",
        )

    def _ensure_local_container_worker_git_key(self, ssh_dir: Path, *, key_name: str) -> str:
        ssh_dir.mkdir(parents=True, exist_ok=True)
        ssh_dir.chmod(0o700)
        key_path = ssh_dir / key_name
        pub_path = ssh_dir / f"{key_name}.pub"
        legacy_key_path = ssh_dir / "agent-sandbox-git"
        legacy_pub_path = ssh_dir / "agent-sandbox-git.pub"
        if not key_path.exists() and legacy_key_path.is_file():
            shutil.copyfile(legacy_key_path, key_path)
            key_path.chmod(0o600)
        if key_path.exists() and not pub_path.exists() and legacy_pub_path.is_file():
            shutil.copyfile(legacy_pub_path, pub_path)
            pub_path.chmod(0o644)
        if not key_path.exists() and pub_path.exists():
            raise ValueError(f"worker Git private key is missing: {key_path}")
        if not key_path.exists():
            subprocess.run(
                [
                    "ssh-keygen",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-C",
                    f"{key_name}@local-container-worker",
                    "-f",
                    str(key_path),
                ],
                check=True,
            )
        elif not pub_path.exists():
            completed = subprocess.run(
                ["ssh-keygen", "-y", "-f", str(key_path)],
                capture_output=True,
                text=True,
                check=True,
            )
            pub_path.write_text(
                f"{completed.stdout.strip()} {key_name}@local-container-worker\n",
                encoding="utf-8",
            )
        key_path.chmod(0o600)
        pub_path.chmod(0o644)
        return pub_path.read_text(encoding="utf-8").strip()

    def _write_local_container_worker_git_ssh_config(
        self,
        config_path: Path,
        *,
        git_ssh_alias: str,
        git_host: str,
        git_user: str,
        identity_file: str,
    ) -> None:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.parent.chmod(0o700)
        begin = "# BEGIN evander local worker git host"
        end = "# END evander local worker git host"
        text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
        for marker_begin, marker_end in (
            ("# BEGIN evander git host", "# END evander git host"),
            (begin, end),
        ):
            while marker_begin in text and marker_end in text:
                start = text.index(marker_begin)
                stop = text.index(marker_end, start) + len(marker_end)
                suffix = "\n" if stop < len(text) and text[stop : stop + 1] == "\n" else ""
                text = text[:start].rstrip() + "\n" + text[stop + len(suffix) :].lstrip("\n")
        block = "\n".join(
            [
                begin,
                f"Host {git_ssh_alias}",
                f"  HostName {git_host}",
                f"  User {git_user}",
                f"  IdentityFile {identity_file}",
                "  IdentitiesOnly yes",
                "  StrictHostKeyChecking accept-new",
                end,
                "",
            ]
        )
        content = block if not text.strip() else block + "\n" + text.strip() + "\n"
        config_path.write_text(content, encoding="utf-8")
        config_path.chmod(0o600)

    def _local_container_worker_unit_content(
        self, state: SetupState, *, run_user: str | None = None
    ) -> str:
        run_user = run_user or _setup_run_user()
        container_user = _container_user_for_run_user(run_user)
        git_root = _local_worker_git_root(self.project_dir, self.runtime_dir)
        git_ssh_alias = _local_worker_git_ssh_alias()
        git_ssh_source_dir = _local_worker_git_ssh_source_dir(run_user)
        allowed_git_source_prefix = f"ssh://{git_ssh_alias}{git_root}/"
        allowed_git_write_source_prefix = f"ssh://{git_ssh_alias}-write{git_root}/"
        codex_auth_image = str(
            state.provider_config.get(
                "container_worker_default_image", DEFAULT_CONTAINER_WORKER_RUNNER_IMAGE
            )
            or DEFAULT_CONTAINER_WORKER_RUNNER_IMAGE
        ).strip()
        worker_host, worker_port = _local_container_worker_bind(state)
        readonly_mount_args = " ".join(
            f"--readonly-mount {host_path}:{container_path}"
            for host_path, container_path in LOCAL_CONTAINER_WORKER_READONLY_MOUNTS
        )
        return "\n".join(
            [
                "[Unit]",
                "Description=Evander Local Container Worker Daemon",
                "After=network-online.target docker.service",
                "Wants=network-online.target docker.service",
                "Requires=docker.service",
                "",
                "[Service]",
                "Type=simple",
                f"User={run_user}",
                "SupplementaryGroups=docker",
                f"WorkingDirectory={self.project_dir}",
                f"EnvironmentFile=-{self.secret_file_path(state)}",
                f"Environment=EVANDER_WORKER_CODEX_AUTH_IMAGE={codex_auth_image}",
                (
                    f"ExecStart={self.project_dir}/.venv/bin/evander-worker-daemon "
                    f"--host {worker_host} "
                    f"--port {worker_port} "
                    f"--runtime-dir {LOCAL_CONTAINER_WORKER_RUNTIME_DIR} "
                    f"--worker-root {LOCAL_CONTAINER_WORKER_ROOT} "
                    "--sandbox-profile default "
                    f"--container-user {container_user} "
                    f"--allowed-git-source-prefix {allowed_git_source_prefix} "
                    f"--allowed-git-source-prefix {allowed_git_write_source_prefix} "
                    f"--git-ssh-source-dir {git_ssh_source_dir} "
                    f"--codex-auth-image {codex_auth_image} "
                    f"{readonly_mount_args}"
                ),
                "Restart=on-failure",
                "RestartSec=5",
                "",
                "[Install]",
                "WantedBy=multi-user.target",
                "",
            ]
        )

    def _provider_payload(self, state: SetupState) -> dict[str, object]:
        payload: dict[str, object] = dict(state.provider_config)
        payload["execution_backend"] = state.execution.get("execution_backend") or "libvirt_vm"
        provider_mode = {
            "openai": "openai_public",
            "anthropic": "anthropic",
            "ollama": "local_openai",
        }.get(state.default_provider, "openai_public")
        payload["model_provider_mode"] = provider_mode
        if payload["execution_backend"] == "container_worker_vm":
            payload.setdefault(
                "container_worker_base_url",
                state.execution.get("container_worker_base_url") or DEFAULT_CONTAINER_WORKER_URL,
            )
            payload.setdefault(
                "container_worker_token_env",
                state.execution.get("container_worker_token_env")
                or DEFAULT_CONTAINER_WORKER_TOKEN_ENV,
            )
            payload.setdefault(
                "container_worker_default_image", DEFAULT_CONTAINER_WORKER_RUNNER_IMAGE
            )
            payload.setdefault(
                "container_worker_images",
                {
                    "default": DEFAULT_CONTAINER_WORKER_RUNNER_IMAGE,
                    "claude": "evander-runner-claude:latest",
                },
            )
        return payload

    def _wait_admin_web(self, state: SetupState) -> None:
        host = str(state.admin_web.get("host") or DEFAULT_ADMIN_HOST)
        url_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        url = f"http://{url_host}:{state.admin_web.get('port') or DEFAULT_ADMIN_PORT}/healthz"
        deadline = time.monotonic() + 60
        last_exc: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with httpx.Client(timeout=3) as client:
                    response = client.get(url)
                if response.status_code == 200:
                    return
            except Exception as exc:
                last_exc = exc
            time.sleep(2)
        raise RuntimeError(f"Admin Web did not become healthy at {url}: {last_exc}")

    def _verify_admin_assistant(self, state: SetupState) -> None:
        admin_runtime = build_admin_runtime(self.runtime_dir)
        backend = str(state.execution.get("execution_backend") or "libvirt_vm")
        user_id = str(state.admin_web.get("username") or "admin")
        thread_id = "setup-smoke"
        assistant_session = admin_runtime.vm_agent_admin.resolve_chat_assistant_session(
            user_id=user_id,
            backend=backend,
            assistant_scope="admin_chat",
            settings={
                "enabled": True,
                "read_only": True,
                "default_model": state.default_model,
            },
            thread_id=thread_id,
            user_message_id=None,
        )
        message_id = f"setup-smoke-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
        smoke_event = admin_runtime.operations_admin.publish_chat_assistant_message(
            user_id=user_id,
            thread_id=thread_id,
            user_message_id=message_id,
            content="Setup smoke check: confirm that the Admin Assistant path is receiving messages.",
            backend=backend,
            assistant_capability_mode="read-only",
        )
        state.assistant_session_id = str(assistant_session.get("session_id") or "")
        state.assistant_smoke_event_id = str(smoke_event.get("event_id") or "")


def _execution_label(value: str) -> str:
    return {
        "container_same_host": "containers on this host",
        "container_worker_host": "containers on a separate worker host",
        "libvirt_vm": "VM-backed agents",
    }.get(value, value)


def _admin_auth_label(value: str) -> str:
    return {
        "local_password": "local password",
        "none": "no authentication",
    }.get(value, value)


def _setup_install_env(state: SetupState) -> dict[str, str]:
    return {
        "EVANDER_SERVICE_PROFILE": SETUP_SERVICE_PROFILE,
        "EVANDER_SETUP_EXECUTION_BACKEND": str(state.execution.get("execution_backend") or ""),
        "EVANDER_SETUP_EXECUTION_MODEL": str(state.execution.get("model") or ""),
    }


def _same_host_container_worker_selected(state: SetupState) -> bool:
    return (
        str(state.execution.get("execution_backend") or "") == "container_worker_vm"
        and str(state.execution.get("model") or "") == "container_same_host"
    )


def _local_container_worker_bind(state: SetupState) -> tuple[str, int]:
    raw_url = (
        str(state.execution.get("container_worker_base_url") or "").strip()
        or DEFAULT_CONTAINER_WORKER_URL
    )
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            "container_worker_base_url must be an http(s) URL for same-host worker setup"
        )
    if not parsed.hostname:
        raise ValueError("container_worker_base_url must include a host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("container_worker_base_url has an invalid port") from exc
    if port is None:
        raise ValueError("container_worker_base_url must include an explicit port")
    return parsed.hostname, port


def _setup_run_user() -> str:
    return os.environ.get("SUDO_USER") or getpass.getuser() or DEFAULT_ADMIN_USERNAME


def _setup_git_user() -> str:
    return os.environ.get("EVANDER_GIT_USER", "").strip() or _setup_run_user()


def _local_worker_git_ssh_alias() -> str:
    return os.environ.get("EVANDER_GIT_SSH_ALIAS", "").strip() or "evander-git-host"


def _local_worker_git_ssh_source_dir(run_user: str) -> Path:
    return _home_for_user(run_user) / ".ssh"


def _primary_group_for_user(user: str) -> str:
    result = subprocess.run(
        ["id", "-gn", user],
        capture_output=True,
        text=True,
        check=False,
    )
    group = result.stdout.strip() if result.returncode == 0 else ""
    return group or user


def _id_value_for_user(flag: str, user: str) -> str:
    result = subprocess.run(
        ["id", flag, user],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _container_user_for_run_user(user: str) -> str:
    uid = _id_value_for_user("-u", user)
    gid = _id_value_for_user("-g", user)
    if uid and gid:
        return f"{uid}:{gid}"
    return user


def _home_for_user(user: str) -> Path:
    result = subprocess.run(
        ["getent", "passwd", user],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        fields = result.stdout.strip().split(":")
        if len(fields) >= 6 and fields[5]:
            return Path(fields[5])
    if user == (getpass.getuser() or ""):
        return Path.home()
    return Path("/home") / user


def _local_worker_git_root(project_dir: Path, runtime_dir: Path | None = None) -> Path:
    resolved_runtime = Path(runtime_dir or "runtime").expanduser()
    if not resolved_runtime.is_absolute():
        resolved_runtime = project_dir / resolved_runtime
    return resolved_runtime.resolve(strict=False) / "managed-git"


def setup_doctor_payload(
    *, project_dir: Path, runtime_dir: Path = DEFAULT_RUNTIME_DIR
) -> dict[str, object]:
    return SetupManager(project_dir=project_dir, runtime_dir=runtime_dir).doctor()


def setup_plan_payload(
    *, project_dir: Path, runtime_dir: Path = DEFAULT_RUNTIME_DIR
) -> dict[str, object]:
    return SetupManager(project_dir=project_dir, runtime_dir=runtime_dir).plan()
