from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import GatewayConfig, load_config
from .models import LocalIssueRecord, SessionManifest, ToolName, VmActionResult, VmSummary
from .runtime import RuntimeContext, build_runtime
from .storage import JsonStore


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _extract_issue_number(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.isdigit():
        return normalized
    if normalized.startswith("vm-") and normalized[3:].isdigit():
        return normalized[3:]
    match = re.match(r"^issue-([0-9]+)(?:-|$)", normalized)
    if match:
        return match.group(1)
    return None


def _read_issue_number_from_yaml(issue_path: Path) -> str:
    text = issue_path.read_text(encoding="utf-8")
    patterns = [
        r"(?m)^\s*issue_number\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
        r"(?m)^\s*number\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
        r"(?m)^\s*id\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    raise ValueError(
        f"Could not read issue number from YAML file: {issue_path}. "
        "Expected one of: issue_number:, number:, or id:."
    )


def _resolve_issue_number(issue_input: str) -> str:
    candidate = Path(issue_input)
    if candidate.is_file():
        return _read_issue_number_from_yaml(candidate)
    return issue_input


def _resolve_vm_name(target: str) -> str:
    normalized = target.strip()
    if normalized.isdigit():
        return f"vm-{normalized}"
    return normalized


def _caller_home() -> Path:
    caller_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
    caller_home = os.environ.get("HOME", "")
    if caller_user:
        try:
            passwd_entry = subprocess.run(
                ["getent", "passwd", caller_user],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            passwd_entry = None
        if passwd_entry and passwd_entry.returncode == 0:
            fields = (passwd_entry.stdout or "").strip().split(":")
            if len(fields) >= 6 and fields[5]:
                caller_home = fields[5]
    return Path(caller_home or Path.home())


def _resolve_private_key_path() -> Path | None:
    configured = os.environ.get("SSH_PRIVATE_KEY_PATH", "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_file() else None

    home = _caller_home()
    for candidate in (home / ".ssh" / "id_ed25519", home / ".ssh" / "id_rsa"):
        if candidate.is_file():
            return candidate
    return None


def _known_hosts_path() -> Path:
    configured = os.environ.get("AGENT_SANDBOX_GUEST_KNOWN_HOSTS_FILE", "").strip()
    if configured:
        path = Path(configured)
    else:
        path = _caller_home() / ".ssh" / "agent-sandbox-known_hosts"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _run_subprocess(
    cmd: list[str],
    *,
    check: bool = False,
    capture_output: bool = True,
    text: bool = True,
    timeout: float | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
        input=input,
    )


def _run_virsh(args: list[str]) -> subprocess.CompletedProcess[str]:
    commands = [["virsh", *args], ["sudo", "-n", "virsh", *args]]
    last_result: subprocess.CompletedProcess[str] | None = None
    for cmd in commands:
        try:
            result = _run_subprocess(cmd)
        except OSError:
            continue
        last_result = result
        if result.returncode == 0:
            return result
    if last_result is None:
        raise RuntimeError(f"Unable to execute virsh command: {' '.join(args)}")
    return last_result


def _virsh_lines(args: list[str]) -> list[str]:
    result = _run_virsh(args)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or f"virsh {' '.join(args)} failed")
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def _dominfo_field(vm_name: str, field_name: str) -> str | None:
    result = _run_virsh(["dominfo", vm_name])
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition(":")
        if key.strip() == field_name:
            stripped = value.strip()
            return stripped or None
    return None


def _resolve_vm_ip(vm_name: str) -> str | None:
    result = _run_virsh(["domifaddr", vm_name])
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if "ipv4" in parts and len(parts) >= 4:
            address = parts[3]
            return address.split("/", 1)[0]
    return None


def _ssh_options(vm_name: str, vm_ip: str, ssh_private_key_path: Path) -> list[str]:
    return [
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={_known_hosts_path()}",
        "-o",
        f"HostKeyAlias={vm_name}",
        "-o",
        "CheckHostIP=no",
        "-o",
        "ConnectTimeout=5",
        "-i",
        str(ssh_private_key_path),
    ]


def _probe_agent_state(vm_name: str, vm_ip: str, vm_user: str) -> tuple[str, list[str], datetime | None]:
    ssh_private_key_path = _resolve_private_key_path()
    if ssh_private_key_path is None:
        return "unknown", [], None

    remote_script = r"""
set -euo pipefail

LOG_DIR="${HOME}/.agent-sandbox"
CONTROL_STATE_PATH="${LOG_DIR}/agent-control.state"
STATE_MAX_AGE_SECONDS="${VM_AGENT_STATE_MAX_AGE_SECONDS:-600}"

read_state_value() {
  local state_path="$1"
  local key="$2"
  [[ -f "${state_path}" ]] || return 1
  awk -F= -v target_key="${key}" '$1 == target_key {print substr($0, index($0, "=") + 1); exit}' "${state_path}" 2>/dev/null
}

is_active_agent_pid() {
  local pid="$1"
  local cmdline_path="/proc/${pid}/cmdline"
  local cmdline=""

  [[ -n "${pid}" && "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1 || return 1
  [[ -r "${cmdline_path}" ]] || return 1
  cmdline="$(tr '\000' ' ' < "${cmdline_path}" 2>/dev/null || true)"
  [[ -n "${cmdline}" ]] || return 1
  [[ "${cmdline}" == *"agent_runner_exec"* || "${cmdline}" == *"agent_runner_resume_last"* ]]
}

iso_to_epoch() {
  local raw="$1"
  [[ -n "${raw}" ]] || return 1
  date -u -d "${raw}" +%s 2>/dev/null
}

state_is_fresh() {
  local updated_at="$1"
  local updated_at_epoch now
  updated_at_epoch="$(iso_to_epoch "${updated_at}")" || return 1
  now="$(date -u +%s)"
  (( now - updated_at_epoch <= STATE_MAX_AGE_SECONDS ))
}

if [[ ! -d "${LOG_DIR}" ]]; then
  printf '{"state":"not_started","repos":[],"updated_at":null}\n'
  exit 0
fi

control_status=""
control_updated_at=""
if [[ -f "${CONTROL_STATE_PATH}" ]]; then
  control_status="$(read_state_value "${CONTROL_STATE_PATH}" "status" || true)"
  control_updated_at="$(read_state_value "${CONTROL_STATE_PATH}" "updated_at" || true)"
  if [[ "${control_status}" == "disabled" ]]; then
    printf '{"state":"disabled","repos":[],"updated_at":%s}\n' "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${control_updated_at}")"
    exit 0
  fi
fi

shopt -s nullglob
active_entries=()
newest_updated_at="${control_updated_at}"
found_any_state=0
for state_path in "${LOG_DIR}"/agent-*.state; do
  found_any_state=1
  status="$(read_state_value "${state_path}" "status" || true)"
  updated_at="$(read_state_value "${state_path}" "updated_at" || true)"
  pid="$(read_state_value "${state_path}" "pid" || true)"
  if [[ -n "${updated_at}" && ( -z "${newest_updated_at}" || "${updated_at}" > "${newest_updated_at}" ) ]]; then
    newest_updated_at="${updated_at}"
  fi
  if [[ "${status}" != "running" ]]; then
    continue
  fi
  if ! state_is_fresh "${updated_at}" && ! is_active_agent_pid "${pid}"; then
    continue
  fi
  name="$(basename "${state_path}")"
  name="${name#agent-}"
  name="${name%.state}"
  active_entries+=("${name}")
done
shopt -u nullglob

if [[ ${#active_entries[@]} -gt 0 ]]; then
  repos_json="$(printf '%s\n' "${active_entries[@]}" | python3 -c 'import json,sys; print(json.dumps([line.strip() for line in sys.stdin if line.strip()]))')"
  updated_json="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${newest_updated_at}")"
  printf '{"state":"running","repos":%s,"updated_at":%s}\n' "${repos_json}" "${updated_json}"
  exit 0
fi

if [[ "${found_any_state}" == "1" || -n "${control_updated_at}" ]]; then
  printf '{"state":"idle","repos":[],"updated_at":%s}\n' "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${newest_updated_at}")"
  exit 0
fi

printf '{"state":"not_started","repos":[],"updated_at":null}\n'
"""

    result = _run_subprocess(
        ["ssh", *_ssh_options(vm_name, vm_ip, ssh_private_key_path), f"{vm_user}@{vm_ip}", "bash", "-s"],
        capture_output=True,
        text=True,
        timeout=15.0,
        input=remote_script,
    )
    if result.returncode != 0:
        return "unknown", [], None
    try:
        payload = json.loads((result.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        return "unknown", [], None
    updated_at = payload.get("updated_at")
    return (
        str(payload.get("state") or "unknown"),
        [str(item) for item in payload.get("repos", []) if str(item).strip()],
        datetime.fromisoformat(updated_at) if isinstance(updated_at, str) and updated_at else None,
    )


def _default_issue_session_id(issue_number: str) -> str:
    return f"issue-{issue_number}-{_utc_now():%Y%m%dT%H%M%SZ}-{os.getpid()}"


def _build_issue_session_manifest(
    *,
    cfg: GatewayConfig,
    session_id: str,
    vm_id: str,
    ttl_hours: int | None,
    discord_channels: list[str],
    email_mailboxes: list[str],
    email_folders: list[str],
) -> SessionManifest:
    allowed_tools: list[ToolName] = [ToolName.APPROVAL_SUBMIT, ToolName.APPROVAL_CHECK]
    if discord_channels:
        allowed_tools.extend([ToolName.DISCORD_GET_COMMANDS, ToolName.DISCORD_POST_STATUS])

    email_requested = bool(email_mailboxes or email_folders)
    resolved_email_mailboxes = email_mailboxes or (cfg.email_mailbox_allowlist if email_requested else [])
    resolved_email_folders = email_folders or (
        sorted({folder for folders in cfg.email_folder_allowlist_by_mailbox.values() for folder in folders})
        if email_requested
        else []
    )
    if email_requested:
        allowed_tools.extend(
            [ToolName.EMAIL_LIST_UNREAD, ToolName.EMAIL_GET_MESSAGE, ToolName.EMAIL_CREATE_DRAFT]
        )
        if cfg.email_send_enabled:
            allowed_tools.append(ToolName.EMAIL_SEND_APPROVED)

    return SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        allowed_tools=list(dict.fromkeys(allowed_tools)),
        discord_channel_allowlist=discord_channels,
        email_folder_allowlist=resolved_email_folders,
        email_mailbox_allowlist=resolved_email_mailboxes,
        email_folder_allowlist_by_mailbox=cfg.email_folder_allowlist_by_mailbox,
        email_draft_recipient_allowlist=cfg.email_draft_recipient_allowlist,
        email_draft_domain_allowlist=cfg.email_draft_domain_allowlist,
        email_send_enabled=cfg.email_send_enabled,
        expires_at=_utc_now() + timedelta(hours=ttl_hours or 8),
        metadata={},
    )


class AdminService:
    def __init__(self, config_path: str | Path = Path("config/gateway.yaml")) -> None:
        self.config_path = Path(config_path)
        self.config = load_config(self.config_path)
        self.runtime = build_runtime(self.config)
        self.local_issue_store = JsonStore(self.config.runtime_dir / "local-issues.json", LocalIssueRecord)
        self.local_issue_counter_path = self.config.runtime_dir / "local-issue-counter.txt"
        self.local_issue_yaml_dir = self.config.runtime_dir / "issues"
        self.local_issue_yaml_dir.mkdir(parents=True, exist_ok=True)

    def _session_ids_by_vm(self) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for session_id, session in self.runtime.sessions.read_all().items():
            if session.vm_id:
                mapping.setdefault(session.vm_id, []).append(session_id)
        for session_ids in mapping.values():
            session_ids.sort()
        return mapping

    def list_vms(self, *, include_inactive: bool = True, omit_state: bool = False, vm_user: str = "agent") -> list[VmSummary]:
        vm_names = _virsh_lines(["list", "--all" if include_inactive else "--state-running", "--name"])
        session_ids_by_vm = self._session_ids_by_vm()
        summaries: list[VmSummary] = []
        for vm_name in vm_names:
            libvirt_state = _dominfo_field(vm_name, "State") or "unknown"
            ip_address = _resolve_vm_ip(vm_name) if libvirt_state == "running" else None
            if libvirt_state != "running":
                agent_state = "inactive_vm"
                repo_names: list[str] = []
                updated_at = None
            elif omit_state or not ip_address:
                agent_state = "unknown" if not ip_address else "omitted"
                repo_names = []
                updated_at = None
            else:
                agent_state, repo_names, updated_at = _probe_agent_state(vm_name, ip_address, vm_user)
            summaries.append(
                VmSummary(
                    vm_name=vm_name,
                    issue_number=_extract_issue_number(vm_name),
                    session_ids=session_ids_by_vm.get(vm_name, []),
                    libvirt_state=libvirt_state,
                    ip_address=ip_address,
                    agent_state=agent_state,
                    agent_repo_names=repo_names,
                    last_state_update_at=updated_at,
                )
            )
        return summaries

    def start_vm(self, target: str) -> VmActionResult:
        vm_name = _resolve_vm_name(target)
        current_state = _dominfo_field(vm_name, "State")
        if current_state is None:
            return VmActionResult(success=False, vm_name=vm_name, action="start", message=f"VM not found: {vm_name}")
        if current_state == "running":
            return VmActionResult(success=True, vm_name=vm_name, action="start", message=f"VM already running: {vm_name}")
        result = _run_virsh(["start", vm_name])
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            return VmActionResult(success=False, vm_name=vm_name, action="start", message=stderr or f"Failed to start {vm_name}")
        return VmActionResult(success=True, vm_name=vm_name, action="start", message=f"Started VM {vm_name}")

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> VmActionResult:
        vm_name = _resolve_vm_name(target)
        current_state = _dominfo_field(vm_name, "State")
        if current_state is None:
            return VmActionResult(success=False, vm_name=vm_name, action="shutdown", message=f"VM not found: {vm_name}")
        if current_state != "running":
            return VmActionResult(success=True, vm_name=vm_name, action="shutdown", message=f"VM already stopped: {vm_name}")
        result = _run_virsh(["shutdown", vm_name])
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            return VmActionResult(success=False, vm_name=vm_name, action="shutdown", message=stderr or f"Failed to request shutdown for {vm_name}")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            state = _dominfo_field(vm_name, "State")
            if state and state != "running":
                return VmActionResult(success=True, vm_name=vm_name, action="shutdown", message=f"Shut down VM {vm_name}")
            time.sleep(2)
        return VmActionResult(
            success=False,
            vm_name=vm_name,
            action="shutdown",
            message=f"Timed out waiting for {vm_name} to shut down after {timeout_seconds}s",
        )

    def _run_script(self, script_name: str, args: list[str]) -> VmActionResult:
        script_path = Path(__file__).resolve().parents[2] / "scripts" / script_name
        completed = _run_subprocess([str(script_path), *args], capture_output=True, text=True)
        issue_target = args[1] if len(args) > 1 else ""
        issue_number = _extract_issue_number(issue_target) or issue_target
        vm_name = _resolve_vm_name(issue_number) if issue_number else ""
        message = (completed.stdout or completed.stderr or "").strip()
        return VmActionResult(
            success=completed.returncode == 0,
            vm_name=vm_name,
            action=script_name.removesuffix(".sh"),
            message=message or f"{script_name} exited with code {completed.returncode}",
        )

    def enable_agent(self, target: str, *, vm_user: str = "agent") -> VmActionResult:
        issue_number = _extract_issue_number(target) or target
        return self._run_script("set_issue_agent_state.sh", ["enable", issue_number, vm_user])

    def disable_agent(self, target: str, *, vm_user: str = "agent") -> VmActionResult:
        issue_number = _extract_issue_number(target) or target
        return self._run_script("set_issue_agent_state.sh", ["disable", issue_number, vm_user])

    def resume_agent(self, target: str, prompt: str, *, vm_user: str = "agent") -> VmActionResult:
        issue_number = _extract_issue_number(target) or target
        return self._run_script("resume_issue_agent.sh", [prompt, issue_number, vm_user])

    def list_local_issues(self) -> list[LocalIssueRecord]:
        return sorted(self.local_issue_store.read_all().values(), key=lambda issue: issue.issue_number)

    def _next_local_issue_number(self) -> str:
        current = 0
        if self.local_issue_counter_path.exists():
            try:
                current = int(self.local_issue_counter_path.read_text(encoding="utf-8").strip() or "0")
            except ValueError:
                current = 0
        current += 1
        self.local_issue_counter_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_issue_counter_path.write_text(f"{current}\n", encoding="utf-8")
        return str(current)

    def create_local_issue(
        self,
        *,
        title: str = "",
        body: str = "",
        repo_source: str | None = None,
        yaml_path: str | None = None,
    ) -> LocalIssueRecord:
        now = _utc_now()
        if yaml_path:
            source_path = Path(yaml_path)
            issue_number = _read_issue_number_from_yaml(source_path)
            target_yaml_path = self.local_issue_yaml_dir / f"{issue_number}.yaml"
            target_yaml_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
            yaml_text = target_yaml_path.read_text(encoding="utf-8")
            if not title:
                title_match = re.search(r"(?m)^\s*title\s*:\s*[\"']?(.*?)[\"']?\s*$", yaml_text)
                title = title_match.group(1).strip() if title_match else f"Local issue {issue_number}"
            if not body:
                body_match = re.search(r"(?m)^\s*body\s*:\s*[\"']?(.*?)[\"']?\s*$", yaml_text)
                body = body_match.group(1).strip() if body_match else ""
        else:
            if not title.strip():
                raise ValueError("title is required when yaml_path is not provided")
            issue_number = self._next_local_issue_number()
            target_yaml_path = self.local_issue_yaml_dir / f"{issue_number}.yaml"
            yaml_text = (
                f"issue_number: {issue_number}\n"
                f"title: {json.dumps(title.strip())}\n"
                f"body: {json.dumps(body.strip())}\n"
                f"source: local_gui\n"
                f"created_at: {json.dumps(now.isoformat())}\n"
            )
            target_yaml_path.write_text(yaml_text, encoding="utf-8")
        record = LocalIssueRecord(
            issue_number=issue_number,
            title=title.strip() or f"Local issue {issue_number}",
            body=body.strip(),
            yaml_path=str(target_yaml_path),
            repo_source=repo_source.strip() if repo_source else None,
            source="local_gui",
            created_at=now,
            updated_at=now,
        )
        self.local_issue_store.put(record.issue_number, record)
        return record

    def create_issue_vm(
        self,
        *,
        issue: str,
        session_id: str | None = None,
        repo_source: str | None = None,
        vm_user: str = "agent",
        ttl_hours: int | None = None,
        discord_channels: list[str] | None = None,
        email_mailboxes: list[str] | None = None,
        email_folders: list[str] | None = None,
        dotnet: bool = False,
        nuget: str | None = None,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        frontend_deps: bool = False,
        frontend_host_checkout: str | None = None,
        frontend_install_roots: list[str] | None = None,
        start_agent: bool = True,
    ) -> VmActionResult:
        issue_number = _resolve_issue_number(issue)
        resolved_session_id = session_id or _default_issue_session_id(issue_number)
        resolved_vm_name = f"vm-{issue_number}"
        if self.runtime.sessions.get(resolved_session_id) is not None:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_issue_vm",
                message=f"Session already exists: {resolved_session_id}",
            )
        if mssql and not os.environ.get("MSSQL_SA_PASSWORD", "").strip():
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_issue_vm",
                message="MSSQL_SA_PASSWORD must be set when --mssql is used.",
            )

        manifest = _build_issue_session_manifest(
            cfg=self.config,
            session_id=resolved_session_id,
            vm_id=resolved_vm_name,
            ttl_hours=ttl_hours,
            discord_channels=discord_channels or [],
            email_mailboxes=email_mailboxes or [],
            email_folders=email_folders or [],
        )
        self.runtime.sessions.put(resolved_session_id, manifest)

        setup_script = Path(__file__).resolve().parents[2] / "scripts" / "setup_issue_vm.sh"
        cmd = [str(setup_script)]
        if not start_agent:
            cmd.append("--dry-run")
        if dotnet:
            cmd.append("--dotnet")
        if nuget is not None:
            cmd.append("--nuget")
        if mssql:
            cmd.append("--mssql")
        if mssql_init_root is not None:
            cmd.extend(["--mssql-init-root", mssql_init_root])
        if playwright:
            cmd.append("--playwright")
        if frontend_deps:
            cmd.append("--frontend-deps")
        if frontend_host_checkout is not None:
            cmd.extend(["--frontend-host-checkout", frontend_host_checkout])
        for install_root in frontend_install_roots or []:
            cmd.extend(["--frontend-install-root", install_root])
        cmd.append(issue)
        if nuget is not None:
            cmd.append(nuget)
        if repo_source is not None:
            cmd.append(repo_source)
        cmd.extend([resolved_session_id, vm_user])
        completed = _run_subprocess(cmd, capture_output=True, text=True)
        message = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_issue_vm",
                message=message or f"setup_issue_vm.sh exited with code {completed.returncode}",
            )
        return VmActionResult(
            success=True,
            vm_name=resolved_vm_name,
            action="create_issue_vm",
            message=message or f"Created issue VM {resolved_vm_name}",
        )

    def create_issue_vm_from_local_issue(
        self,
        issue_number: str,
        *,
        vm_user: str = "agent",
        start_agent: bool = True,
    ) -> VmActionResult:
        issue = self.local_issue_store.get(issue_number)
        if issue is None:
            return VmActionResult(
                success=False,
                vm_name=f"vm-{issue_number}",
                action="create_issue_vm",
                message=f"Unknown local issue: {issue_number}",
            )
        return self.create_issue_vm(
            issue=issue.yaml_path,
            repo_source=issue.repo_source,
            vm_user=vm_user,
            start_agent=start_agent,
        )
