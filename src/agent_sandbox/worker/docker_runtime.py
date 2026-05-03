from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from typing import Any

from .models import WorkerJobRecord, WorkerJobRequest, WorkerSessionPaths
from .sandbox_profile import (
    WorkerReadOnlyMount,
    WorkerSandboxProfile,
    default_worker_sandbox_profile,
    parse_worker_read_only_mount,
    reject_unsafe_mount,
    validate_worker_read_only_mount,
    validate_worker_sandbox_profile,
)


class DockerContainerRuntime:
    def __init__(
        self,
        *,
        docker_network: str | None = "none",
        docker_binary: str = "docker",
        sandbox_profile: WorkerSandboxProfile | None = None,
        read_only_mounts: Sequence[str | WorkerReadOnlyMount] | None = None,
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

    def start_job(
        self,
        request: WorkerJobRequest,
        record: WorkerJobRecord,
        paths: WorkerSessionPaths,
    ) -> str:
        args = self._build_run_args(request=request, record=record, paths=paths)
        completed = subprocess.run(args, check=True, capture_output=True, text=True)
        return completed.stdout.strip()

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
        return {
            "status": docker_state_to_status(state),
            "raw_state": {
                "status": state.get("Status"),
                "running": state.get("Running"),
                "exit_code": state.get("ExitCode"),
            },
        }

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

    def get_logs(self, record: WorkerJobRecord) -> str:
        completed = subprocess.run(
            [self.docker_binary, "logs", record.container_id or record.container_name],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout

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
            self.sandbox_profile.workdir,
        ]
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
        args.append(request.image)
        args.extend(request.command)
        return args


def docker_state_to_status(state: dict[str, Any]) -> str:
    if state.get("Running"):
        return "running"
    status = str(state.get("Status") or "").lower()
    if status in {"exited", "dead"}:
        exit_code = state.get("ExitCode")
        return "exited" if exit_code in (0, None) else "failed"
    return status or "unknown"
