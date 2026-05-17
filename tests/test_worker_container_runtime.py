from __future__ import annotations

from pathlib import Path

import pytest

from agent_sandbox.worker_daemon import (
    DockerContainerRuntime,
    WorkerJobRecord,
    WorkerJobRequest,
    WorkerResourceLimits,
    WorkerSandboxProfile,
    WorkerSessionPaths,
    strict_worker_sandbox_profile,
)


def _request() -> WorkerJobRequest:
    return WorkerJobRequest.model_validate(
        {
            "session_id": "session-1",
            "job_id": "job-1",
            "image": "codex:latest",
            "command": ["codex", "exec", "hello"],
            "resource_limits": {"memory": "512m", "cpus": 1.5, "pids_limit": 256},
        }
    )


def _record(paths: WorkerSessionPaths) -> WorkerJobRecord:
    return WorkerJobRecord(
        job_id="job-1",
        session_id="session-1",
        image="codex:latest",
        command=["codex", "exec", "hello"],
        container_name="agent-worker-job-1",
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
    )


def test_docker_runtime_refuses_host_network() -> None:
    with pytest.raises(ValueError, match="host"):
        DockerContainerRuntime(docker_network="host")


def test_docker_runtime_defaults_to_none_network() -> None:
    runtime = DockerContainerRuntime()

    assert runtime.docker_network == "none"
    assert runtime.sandbox_profile.network_mode == "none"


def test_docker_run_args_use_restricted_container_shape_and_persistent_mounts(
    tmp_path: Path,
) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    runtime = DockerContainerRuntime(docker_network="none")

    args = runtime._build_run_args(request=_request(), record=_record(paths), paths=paths)  # noqa: SLF001

    assert args[:3] == ["docker", "run", "-d"]
    assert "--privileged" not in args
    assert "--read-only" not in args
    assert "/var/run/docker.sock" not in " ".join(args)
    assert "--network" in args
    assert args[args.index("--network") + 1] == "none"
    assert "--cap-drop" in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in args
    assert args[args.index("--security-opt") + 1] == "no-new-privileges"
    assert not any("/workspace" in arg for arg in args)
    assert f"{paths.agent_home}:/home/agent" in args
    assert f"{paths.artifacts}:/artifacts" in args
    assert args[args.index("-w") + 1] == "/home/agent"
    assert args[args.index("--memory") + 1] == "512m"
    assert args[args.index("--cpus") + 1] == "1.5"
    assert args[args.index("--pids-limit") + 1] == "256"
    assert args[-4:] == ["codex:latest", "codex", "exec", "hello"]


def test_docker_runtime_allows_non_host_network_override(tmp_path: Path) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    runtime = DockerContainerRuntime(docker_network="agent-worker-net")

    args = runtime._build_run_args(request=_request(), record=_record(paths), paths=paths)  # noqa: SLF001

    assert args[args.index("--network") + 1] == "agent-worker-net"


def test_docker_runtime_adds_worker_read_only_mounts(tmp_path: Path) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    runtime = DockerContainerRuntime(
        docker_network="none",
        read_only_mounts=[
            "/cache/dotnet:/opt/agent-sandbox/toolchains/dotnet",
            "/cache/node:/opt/agent-sandbox/toolchains/node-v24.14.0:ro",
        ],
    )

    args = runtime._build_run_args(request=_request(), record=_record(paths), paths=paths)  # noqa: SLF001

    assert "-v" in args
    assert "/cache/dotnet:/opt/agent-sandbox/toolchains/dotnet:ro" in args
    assert "/cache/node:/opt/agent-sandbox/toolchains/node-v24.14.0:ro" in args


def test_docker_runtime_mounts_passwd_for_numeric_container_user(tmp_path: Path) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    runtime = DockerContainerRuntime(
        docker_network="none",
        sandbox_profile=WorkerSandboxProfile(name="default", user="1001:1001"),
    )

    args = runtime._build_run_args(request=_request(), record=_record(paths), paths=paths)  # noqa: SLF001

    passwd_path = paths.root / "container-identity" / "passwd"
    group_path = paths.root / "container-identity" / "group"
    assert f"{passwd_path}:/etc/passwd:ro" in args
    assert f"{group_path}:/etc/group:ro" in args
    assert "agent:x:1001:1001:Agent Sandbox:/home/agent:/bin/bash" in passwd_path.read_text(
        encoding="utf-8"
    )
    assert "agent:x:1001:" in group_path.read_text(encoding="utf-8")
    assert args[args.index("--user") + 1] == "1001:1001"


def test_docker_runtime_rejects_unsafe_worker_read_only_mounts() -> None:
    with pytest.raises(ValueError, match="Docker socket"):
        DockerContainerRuntime(read_only_mounts=["/var/run/docker.sock:/opt/docker.sock"])

    with pytest.raises(ValueError, match="/home/agent"):
        DockerContainerRuntime(read_only_mounts=["/cache/home:/home/agent/tooling"])


def test_strict_profile_adds_read_only_tmpfs_and_default_pids_limit(tmp_path: Path) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    request = _request()
    request.resource_limits = WorkerResourceLimits(memory="512m", cpus=1.5)
    runtime = DockerContainerRuntime(
        sandbox_profile=strict_worker_sandbox_profile(), docker_network=None
    )

    args = runtime._build_run_args(request=request, record=_record(paths), paths=paths)  # noqa: SLF001

    assert "--read-only" in args
    assert args[args.index("--tmpfs") + 1] == "/tmp:rw,noexec,nosuid,size=512m"
    assert args[args.index("--security-opt") + 1] == "no-new-privileges"
    assert args[args.index("--pids-limit") + 1] == "512"


def test_job_pids_limit_overrides_strict_profile_default(tmp_path: Path) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    runtime = DockerContainerRuntime(
        sandbox_profile=strict_worker_sandbox_profile(), docker_network=None
    )

    args = runtime._build_run_args(request=_request(), record=_record(paths), paths=paths)  # noqa: SLF001

    assert args[args.index("--pids-limit") + 1] == "256"


def test_profile_refuses_host_network() -> None:
    with pytest.raises(ValueError, match="host"):
        WorkerSandboxProfile(name="bad", network_mode="host").with_overrides()


def test_profile_refuses_privileged_mode() -> None:
    with pytest.raises(ValueError, match="privileged"):
        DockerContainerRuntime(
            sandbox_profile=WorkerSandboxProfile(name="bad", privileged=True), docker_network=None
        )


def test_docker_runtime_rejects_docker_socket_mount(tmp_path: Path) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    unsafe_paths = WorkerSessionPaths(
        session_id=paths.session_id,
        root=paths.root,
        agent_home=Path("/var/run/docker.sock"),
        repo_root=paths.repo_root,
        artifacts=paths.artifacts,
        logs=paths.logs,
        metadata_path=paths.metadata_path,
    )
    runtime = DockerContainerRuntime()

    with pytest.raises(ValueError, match="Docker socket"):
        runtime._build_run_args(request=_request(), record=_record(paths), paths=unsafe_paths)  # noqa: SLF001
