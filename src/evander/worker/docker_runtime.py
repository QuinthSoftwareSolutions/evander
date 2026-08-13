from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from evander.codex_auth import CodexTurnSpec

from .codex_subscription import (
    CODEX_COMPLETION_MARKER_CONTAINER_DIR,
    CODEX_COMPLETION_MARKER_HOST_DIRNAME,
)
from .models import (
    WorkerFailureCause,
    WorkerJobRecord,
    WorkerJobRequest,
    WorkerSessionPaths,
    ensure_resolved_path_within,
)
from .sandbox_profile import (
    WorkerReadOnlyMount,
    WorkerSandboxProfile,
    default_worker_sandbox_profile,
    parse_worker_read_only_mount,
    reject_unsafe_mount,
    validate_worker_read_only_mount,
    validate_worker_sandbox_profile,
)


_CODEX_IMAGE_BINARY = "/usr/local/bin/codex"
_CODEX_HOME_CONTAINER_PATH = "/home/agent/.config/evander/codex"
_CODEX_CONFIG_CONTAINER_PATH = f"{_CODEX_HOME_CONTAINER_PATH}/config.toml"
_CODEX_LEGACY_CONFIG_CONTAINER_PATH = "/home/agent/.codex/config.toml"
_CODEX_SUBSCRIPTION_CONFIG_HOST_FILENAME = "codex-subscription-config.toml"
_CODEX_SUBSCRIPTION_PROJECT_CONFIG_HOST_FILENAME = "codex-subscription-project-config.toml"
_PIDS_EXHAUSTION_MARKER_SUFFIX = ".pids-limit-exhausted"
_ACTIVE_TOOL_INTERRUPTION_MARKER_SUFFIX = ".active-tool-interruption.json"
_PIDS_EVENT_POLL_SECONDS = 0.05
_PIDS_MONITOR_TERMINAL_JOIN_SECONDS = 0.25
_TEST_COMMAND_PATTERN = re.compile(
    r"(?:^|\s)(?:dotnet\s+test|pytest(?:\s|$)|python(?:3)?\s+-m\s+pytest|"
    r"npm\s+(?:run\s+)?test|npx\s+(?:vitest|jest)|cargo\s+test|go\s+test)(?:\s|$)",
    re.IGNORECASE,
)
_CODEX_PROCESS_PATTERN = re.compile(
    r"(?:^|[\s/])(codex|codex-code-mode-host)(?:[\s/]|$)", re.IGNORECASE
)


class DockerContainerRuntime:
    def __init__(
        self,
        *,
        docker_network: str | None = "none",
        docker_binary: str = "docker",
        sandbox_profile: WorkerSandboxProfile | None = None,
        read_only_mounts: Sequence[str | WorkerReadOnlyMount] | None = None,
        codex_egress_proxy_url: str | None = None,
        proc_root: Path = Path("/proc"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
    ) -> None:
        profile = sandbox_profile or default_worker_sandbox_profile()
        if docker_network is not None:
            profile = profile.with_overrides(network_mode=docker_network)
        self.sandbox_profile = validate_worker_sandbox_profile(profile)
        self.docker_network = self.sandbox_profile.network_mode
        self.docker_binary = docker_binary
        self.read_only_mounts = tuple(
            parse_worker_read_only_mount(mount)
            if isinstance(mount, str)
            else validate_worker_read_only_mount(mount)
            for mount in (read_only_mounts or ())
        )
        self.codex_egress_proxy_url = _normalize_proxy_url(codex_egress_proxy_url)
        self.proc_root = Path(proc_root)
        self.cgroup_root = Path(cgroup_root)
        self._pids_monitors: dict[str, threading.Thread] = {}
        self._pids_monitors_lock = threading.Lock()

    def start_job(
        self,
        request: WorkerJobRequest,
        record: WorkerJobRecord,
        paths: WorkerSessionPaths,
    ) -> str:
        self._pids_exhaustion_marker(record).unlink(missing_ok=True)
        args = self._build_run_args(request=request, record=record, paths=paths)
        completed = subprocess.run(args, check=True, capture_output=True, text=True)
        container_id = completed.stdout.strip()
        if self._effective_pids_limit(request) is not None:
            self._start_pids_event_monitor(record, container_id)
        return container_id

    def inspect_job(self, record: WorkerJobRecord) -> dict[str, Any]:
        target = record.container_id or record.container_name
        completed = subprocess.run(
            [self.docker_binary, "inspect", target],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout or "[]")
        if not payload:
            return {"status": "unknown"}
        state = payload[0].get("State", {})
        exit_code = state.get("ExitCode")
        oom_killed = bool(state.get("OOMKilled"))
        runtime_error = str(state.get("Error") or "").strip() or None
        terminal = not bool(state.get("Running")) and str(state.get("Status") or "").casefold() in {
            "exited",
            "dead",
        }
        if terminal:
            self._join_pids_event_monitor(record)
        pids_limit_exhausted = (
            terminal
            and exit_code not in (0, None)
            and self._pids_exhaustion_marker(record).is_file()
        )
        return {
            "status": docker_state_to_status(state),
            "exit_code": exit_code,
            "oom_killed": oom_killed,
            "runtime_error": runtime_error,
            "failure_cause": (
                WorkerFailureCause.PIDS_LIMIT_EXHAUSTED.value if pids_limit_exhausted else None
            ),
            "active_tool_interruption": self._active_tool_interruption(record),
            "raw_state": {
                "status": state.get("Status"),
                "running": state.get("Running"),
                "exit_code": exit_code,
                "oom_killed": oom_killed,
            },
        }

    def _effective_pids_limit(self, request: WorkerJobRequest) -> int | None:
        return (
            request.resource_limits.pids_limit
            if request.resource_limits.pids_limit is not None
            else self.sandbox_profile.default_pids_limit
        )

    def _pids_exhaustion_marker(self, record: WorkerJobRecord) -> Path:
        return Path(record.logs_path) / f"{record.job_id}{_PIDS_EXHAUSTION_MARKER_SUFFIX}"

    def _active_tool_interruption_marker(self, record: WorkerJobRecord) -> Path:
        return Path(record.logs_path) / f"{record.job_id}{_ACTIVE_TOOL_INTERRUPTION_MARKER_SUFFIX}"

    def _start_pids_event_monitor(
        self,
        record: WorkerJobRecord,
        container_id: str,
    ) -> None:
        events_path = self._container_pids_events_path(container_id)
        if events_path is None:
            return
        maximum_events = self._pids_max_events(events_path)
        if maximum_events is None:
            return
        monitor = threading.Thread(
            target=self._monitor_pids_events,
            args=(record, events_path, maximum_events),
            name=f"evander-pids-monitor-{record.job_id}",
            daemon=True,
        )
        with self._pids_monitors_lock:
            self._pids_monitors[record.job_id] = monitor
        monitor.start()

    def _monitor_pids_events(
        self,
        record: WorkerJobRecord,
        events_path: Path,
        baseline_maximum_events: int,
    ) -> None:
        unreadable_polls = 0
        try:
            while True:
                maximum_events = self._pids_max_events(events_path)
                if maximum_events is None:
                    # A cgroup counter can be momentarily unreadable while the kernel updates
                    # it. Retrying prevents a missed exhaustion event; a removed cgroup still
                    # terminates the monitor promptly.
                    unreadable_polls += 1
                    if (
                        unreadable_polls >= 20
                        or self._cgroup_is_populated(events_path.parent / "cgroup.events") is False
                    ):
                        return
                    time.sleep(_PIDS_EVENT_POLL_SECONDS)
                    continue
                unreadable_polls = 0
                if maximum_events > baseline_maximum_events:
                    self._record_pids_limit_exhaustion(record)
                    self.interrupt_active_tool(record, reason="pids_limit_event")
                    return
                if self._cgroup_is_populated(events_path.parent / "cgroup.events") is False:
                    return
                time.sleep(_PIDS_EVENT_POLL_SECONDS)
        finally:
            with self._pids_monitors_lock:
                current = self._pids_monitors.get(record.job_id)
                if current is threading.current_thread():
                    self._pids_monitors.pop(record.job_id, None)

    def _join_pids_event_monitor(self, record: WorkerJobRecord) -> None:
        with self._pids_monitors_lock:
            monitor = self._pids_monitors.get(record.job_id)
        if monitor is not None:
            monitor.join(timeout=_PIDS_MONITOR_TERMINAL_JOIN_SECONDS)

    def _container_pids_events_path(self, container_id: str) -> Path | None:
        if not container_id:
            return None
        try:
            completed = subprocess.run(
                [
                    self.docker_binary,
                    "inspect",
                    "--format",
                    "{{.State.Pid}}",
                    container_id,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            pid = int(completed.stdout.strip())
        except (OSError, ValueError, subprocess.CalledProcessError):
            return None
        if pid <= 0:
            return None
        try:
            cgroup_lines = (self.proc_root / str(pid) / "cgroup").read_text(encoding="utf-8")
        except OSError:
            return None
        for line in cgroup_lines.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            hierarchy, controllers, relative_path = parts
            controller_set = {item for item in controllers.split(",") if item}
            if hierarchy == "0" and not controller_set:
                candidate = self._contained_cgroup_path(relative_path) / "pids.events"
            elif "pids" in controller_set:
                candidate = (
                    self._contained_cgroup_path(f"pids/{relative_path.lstrip('/')}") / "pids.events"
                )
            else:
                continue
            if candidate.is_file():
                return candidate
        return None

    def _contained_cgroup_path(self, relative_path: str) -> Path:
        relative = Path(str(relative_path or "").lstrip("/"))
        if ".." in relative.parts:
            return self.cgroup_root / "__invalid__"
        return self.cgroup_root / relative

    @staticmethod
    def _pids_max_events(events_path: Path) -> int | None:
        try:
            lines = events_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines:
            key, separator, raw_value = line.partition(" ")
            if key != "max" or not separator:
                continue
            try:
                return int(raw_value.strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _cgroup_is_populated(events_path: Path) -> bool | None:
        try:
            lines = events_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines:
            key, separator, raw_value = line.partition(" ")
            if key != "populated" or not separator:
                continue
            if raw_value.strip() == "1":
                return True
            if raw_value.strip() == "0":
                return False
            return None
        return None

    def _record_pids_limit_exhaustion(self, record: WorkerJobRecord) -> None:
        marker = self._pids_exhaustion_marker(record)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                WorkerFailureCause.PIDS_LIMIT_EXHAUSTED.value + "\n",
                encoding="utf-8",
            )
        except OSError:
            return

    def interrupt_active_tool(
        self,
        record: WorkerJobRecord,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Terminate only a known test process group, never the Codex host group.

        The command selector is deliberately worker-owned.  The control plane supplies no shell
        command, PID, or process-group value, so this endpoint cannot become arbitrary command
        execution inside a worker container.
        """
        target = record.container_id or record.container_name
        result: dict[str, Any] = {
            "reason": str(reason or "operator_requested").strip() or "operator_requested",
            "interrupted": False,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        try:
            completed = subprocess.run(
                [self.docker_binary, "exec", target, "ps", "-eo", "pid=,ppid=,pgid=,args="],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            result["message"] = "could not inspect container processes"
            self._record_active_tool_interruption(record, result)
            return result
        candidate = self._test_process_group(completed.stdout)
        if candidate is None:
            result["message"] = "no interruptible test process is active"
            self._record_active_tool_interruption(record, result)
            return result
        pgid, command = candidate
        try:
            subprocess.run(
                [self.docker_binary, "exec", target, "kill", "-TERM", f"-{pgid}"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            result.update(
                {
                    "process_group_id": pgid,
                    "command": command,
                    "message": "could not interrupt the active test process",
                }
            )
            self._record_active_tool_interruption(record, result)
            return result
        result.update(
            {
                "interrupted": True,
                "process_group_id": pgid,
                "command": command,
                "message": "sent SIGTERM to the active test process group",
            }
        )
        self._record_active_tool_interruption(record, result)
        return result

    @staticmethod
    def _test_process_group(process_table: str) -> tuple[int, str] | None:
        candidates: list[tuple[int, int, str]] = []
        for raw_line in str(process_table or "").splitlines():
            parts = raw_line.strip().split(None, 3)
            if len(parts) != 4:
                continue
            _pid_text, _parent_pid_text, pgid_text, command = parts
            try:
                pgid = int(pgid_text)
            except ValueError:
                continue
            normalized_command = " ".join(command.split())
            if pgid <= 1 or _CODEX_PROCESS_PATTERN.search(normalized_command):
                continue
            if not _TEST_COMMAND_PATTERN.search(normalized_command):
                continue
            # Prefer the group leader so a child runner cannot select a shared parent group.
            try:
                pid = int(_pid_text)
            except ValueError:
                pid = -1
            candidates.append((0 if pid == pgid else 1, pgid, normalized_command))
        if not candidates:
            return None
        _priority, pgid, command = min(candidates)
        return pgid, command

    def _record_active_tool_interruption(
        self,
        record: WorkerJobRecord,
        interruption: dict[str, Any],
    ) -> None:
        marker = self._active_tool_interruption_marker(record)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps(interruption, sort_keys=True) + "\n", encoding="utf-8")
        except OSError:
            return

    def _active_tool_interruption(self, record: WorkerJobRecord) -> dict[str, Any] | None:
        marker = self._active_tool_interruption_marker(record)
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def stop_job(self, record: WorkerJobRecord) -> None:
        subprocess.run(
            [self.docker_binary, "stop", record.container_id or record.container_name],
            check=True,
            capture_output=True,
            text=True,
        )

    def remove_job(self, record: WorkerJobRecord) -> None:
        subprocess.run(
            [self.docker_binary, "rm", "-f", record.container_id or record.container_name],
            check=True,
            capture_output=True,
            text=True,
        )

    def get_logs(self, record: WorkerJobRecord, *, tail_lines: int | None = None) -> str:
        command = [self.docker_binary, "logs"]
        if tail_lines is not None and tail_lines > 0:
            command.extend(["--tail", str(tail_lines)])
        command.append(record.container_id or record.container_name)
        completed = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return completed.stdout

    def codex_app_server_command(
        self, record: WorkerJobRecord, turn: CodexTurnSpec
    ) -> Sequence[str]:
        if not self.codex_egress_proxy_url:
            raise ValueError("Codex subscription jobs require a restricted codex_egress_proxy_url")
        no_proxy = [
            "127.0.0.1",
            "localhost",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
        ]
        if turn.mcp_gateway_url is not None:
            gateway_host = urlsplit(str(turn.mcp_gateway_url)).hostname
            if gateway_host:
                no_proxy.append(gateway_host)
        return (
            self.docker_binary,
            "exec",
            "-i",
            "-e",
            f"HTTPS_PROXY={self.codex_egress_proxy_url}",
            "-e",
            f"HTTP_PROXY={self.codex_egress_proxy_url}",
            "-e",
            f"NO_PROXY={','.join(dict.fromkeys(no_proxy))}",
            "-e",
            f"CODEX_HOME={_CODEX_HOME_CONTAINER_PATH}",
            record.container_id or record.container_name,
            _CODEX_IMAGE_BINARY,
            "app-server",
            "--stdio",
        )

    def _build_run_args(
        self,
        *,
        request: WorkerJobRequest,
        record: WorkerJobRecord,
        paths: WorkerSessionPaths,
    ) -> list[str]:
        for host_path, container_path in (
            (paths.agent_home, "/home/agent"),
            (paths.artifacts, "/artifacts"),
        ):
            reject_unsafe_mount(host_path, container_path)
        for mount in self.read_only_mounts:
            validate_worker_read_only_mount(mount)
        paths.assert_contained()
        identity_mounts = self._container_identity_mounts(paths)
        subscription_completion_mount: str | None = None
        subscription_config_mounts: tuple[str, ...] = ()
        if (
            request.codex_turn is not None
            and request.codex_turn.auth_mode.value == "chatgpt_subscription"
        ):
            completion_dir = paths.logs / CODEX_COMPLETION_MARKER_HOST_DIRNAME
            ensure_resolved_path_within(
                base=paths.root,
                candidate=completion_dir,
                description="Codex completion marker directory",
            )
            completion_dir.mkdir(parents=True, exist_ok=True)
            ensure_resolved_path_within(
                base=paths.root,
                candidate=completion_dir,
                description="Codex completion marker directory",
            )
            reject_unsafe_mount(completion_dir, CODEX_COMPLETION_MARKER_CONTAINER_DIR)
            subscription_completion_mount = (
                f"{completion_dir}:{CODEX_COMPLETION_MARKER_CONTAINER_DIR}:ro"
            )
            subscription_config_mounts = self._subscription_config_mounts(
                paths,
                request.codex_turn,
            )

        args = [
            self.docker_binary,
            "run",
            "-d",
            "--name",
            record.container_name,
            "--rm=false",
            "--network",
            self.sandbox_profile.network_mode,
            "-v",
            f"{paths.agent_home}:/home/agent",
            "-v",
            f"{paths.artifacts}:/artifacts",
            "-w",
            request.working_directory or self.sandbox_profile.workdir,
        ]
        for mount in identity_mounts:
            args.extend(["-v", mount])
        if subscription_completion_mount is not None:
            args.extend(["-v", subscription_completion_mount])
        for mount in subscription_config_mounts:
            args.extend(["-v", mount])
        for mount in self.read_only_mounts:
            args.extend(["-v", mount.docker_volume_arg])
        for cap in self.sandbox_profile.cap_drop:
            args.extend(["--cap-drop", cap])
        for security_opt in self.sandbox_profile.security_opt:
            args.extend(["--security-opt", security_opt])
        if self.sandbox_profile.read_only_rootfs:
            args.append("--read-only")
        for tmpfs in self.sandbox_profile.tmpfs:
            args.extend(["--tmpfs", tmpfs])
        if self.sandbox_profile.user:
            args.extend(["--user", self.sandbox_profile.user])
        if request.resource_limits.memory:
            args.extend(["--memory", request.resource_limits.memory])
        if request.resource_limits.cpus is not None:
            args.extend(["--cpus", str(request.resource_limits.cpus)])
        pids_limit = request.resource_limits.pids_limit
        if pids_limit is None:
            pids_limit = self.sandbox_profile.default_pids_limit
        if pids_limit is not None:
            args.extend(["--pids-limit", str(pids_limit)])
        if (
            request.codex_turn is not None
            and request.codex_turn.auth_mode.value == "chatgpt_subscription"
        ):
            if not self.codex_egress_proxy_url:
                raise ValueError(
                    "Codex subscription jobs require a restricted codex_egress_proxy_url"
                )
        args.append(request.image)
        args.extend(request.command)
        return args

    def _container_identity_mounts(self, paths: WorkerSessionPaths) -> list[str]:
        identity = _numeric_container_identity(self.sandbox_profile.user)
        if identity is None:
            return []
        uid, gid = identity
        identity_dir = paths.root / "container-identity"
        ensure_resolved_path_within(
            base=paths.root,
            candidate=identity_dir,
            description="container identity path",
        )
        identity_dir.mkdir(parents=True, exist_ok=True)
        ensure_resolved_path_within(
            base=paths.root,
            candidate=identity_dir,
            description="container identity path",
        )
        passwd_path = identity_dir / "passwd"
        group_path = identity_dir / "group"
        _write_container_identity_file(
            passwd_path,
            f"root:x:0:0:root:/root:/bin/bash\nagent:x:{uid}:{gid}:Evander:/home/agent:/bin/bash\n",
        )
        _write_container_identity_file(
            group_path,
            f"root:x:0:\nagent:x:{gid}:\n",
        )
        return [
            f"{passwd_path}:/etc/passwd:ro",
            f"{group_path}:/etc/group:ro",
        ]

    def _subscription_config_mounts(
        self,
        paths: WorkerSessionPaths,
        turn: CodexTurnSpec,
    ) -> tuple[str, ...]:
        """Overlay metered provider config for one subscription job only.

        Container session bootstrap deliberately keeps relay configuration in both
        the active Codex home and the legacy ``~/.codex`` location. A subscription
        app-server must retain the same persisted thread store, but it must not load
        that metered provider or treat the legacy copy as project-local config.
        Worker-owned read-only overlays preserve the thread/skill directories while
        keeping external-token authentication independent of ``OPENAI_API_KEY``.
        The per-session MCP server must be present before app-server starts; a
        thread-resume config override alone does not initialize a newly introduced
        MCP connection.
        """

        config_path = paths.root / _CODEX_SUBSCRIPTION_CONFIG_HOST_FILENAME
        project_config_path = paths.root / _CODEX_SUBSCRIPTION_PROJECT_CONFIG_HOST_FILENAME
        for description, path in (
            ("Codex subscription config", config_path),
            ("Codex subscription project config", project_config_path),
        ):
            ensure_resolved_path_within(
                base=paths.root,
                candidate=path,
                description=description,
            )
        config_content = (
            'forced_login_method = "chatgpt"\n'
            "check_for_update_on_startup = false\n"
            'model_reasoning_effort = "high"\n'
            'personality = "pragmatic"\n'
            "\n"
            '[projects."/home/agent"]\n'
            'trust_level = "trusted"\n'
        )
        if turn.mcp_gateway_url is not None:
            config_content += (
                "\n"
                "[mcp_servers.evander]\n"
                f"url = {json.dumps(str(turn.mcp_gateway_url))}\n"
                "enabled = true\n"
                "required = true\n"
            )
        _write_worker_owned_config(config_path, config_content)
        _write_worker_owned_config(
            project_config_path,
            (
                "# The metered relay provider is intentionally hidden from\n"
                "# subscription-authenticated app-server turns.\n"
            ),
        )
        return (
            f"{config_path}:{_CODEX_CONFIG_CONTAINER_PATH}:ro",
            f"{project_config_path}:{_CODEX_LEGACY_CONFIG_CONTAINER_PATH}:ro",
        )


def _numeric_container_identity(user: str | None) -> tuple[int, int] | None:
    normalized = str(user or "").strip()
    if not normalized:
        return None
    match = re.fullmatch(r"(?P<uid>[0-9]+)(?::(?P<gid>[0-9]+))?", normalized)
    if match is None:
        return None
    uid = int(match.group("uid"))
    gid = int(match.group("gid") or match.group("uid"))
    if uid < 0 or gid < 0:
        return None
    return uid, gid


def _write_container_identity_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o644)


def _write_worker_owned_config(path: Path, content: str) -> None:
    try:
        if path.is_file() and not path.is_symlink():
            if path.read_text(encoding="utf-8") == content:
                path.chmod(0o600)
                return
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def docker_state_to_status(state: dict[str, Any]) -> str:
    if state.get("Running"):
        return "running"
    status = str(state.get("Status") or "").lower()
    if status in {"exited", "dead"}:
        exit_code = state.get("ExitCode")
        return "exited" if exit_code in (0, None) else "failed"
    return status or "unknown"


def _normalize_proxy_url(value: str | None) -> str | None:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return None
    parsed = urlsplit(text)
    if parsed.scheme != "http" or not parsed.hostname or parsed.path not in {"", "/"}:
        raise ValueError("codex_egress_proxy_url must be an http URL without a path")
    return text
