from __future__ import annotations

import json
import subprocess
from typing import Any

from .models import WorkerJobRecord, WorkerJobRequest, WorkerSessionPaths


class DockerContainerRuntime:
    def __init__(self, *, docker_network: str = "none", docker_binary: str = "docker") -> None:
        if docker_network == "host":
            raise ValueError("docker_network='host' is not allowed")
        self.docker_network = docker_network or "none"
        self.docker_binary = docker_binary

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
        args = [
            self.docker_binary,
            "run",
            "-d",
            "--name",
            record.container_name,
            "--rm=false",
            "--cap-drop",
            "ALL",
            "--network",
            self.docker_network,
            "-v",
            f"{paths.workspace}:/workspace",
            "-v",
            f"{paths.agent_home}:/home/agent",
            "-v",
            f"{paths.artifacts}:/artifacts",
            "-w",
            "/workspace",
        ]
        if request.resource_limits.memory:
            args.extend(["--memory", request.resource_limits.memory])
        if request.resource_limits.cpus is not None:
            args.extend(["--cpus", str(request.resource_limits.cpus)])
        if request.resource_limits.pids_limit is not None:
            args.extend(["--pids-limit", str(request.resource_limits.pids_limit)])
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
