from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from evander.codex_auth import CodexAuthMode, CodexTurnSpec
from evander.worker_daemon import (
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


def test_docker_runtime_allows_explicitly_unsafe_host_network(tmp_path: Path) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    runtime = DockerContainerRuntime(
        sandbox_profile=WorkerSandboxProfile(
            name="local-unsafe", network_mode="host", allow_host_network=True
        ),
        docker_network=None,
    )

    args = runtime._build_run_args(request=_request(), record=_record(paths), paths=paths)  # noqa: SLF001

    assert args[args.index("--network") + 1] == "host"


def test_docker_runtime_defaults_to_none_network() -> None:
    runtime = DockerContainerRuntime()

    assert runtime.docker_network == "none"
    assert runtime.sandbox_profile.network_mode == "none"


def test_docker_runtime_exposes_terminal_exit_classification_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    runtime = DockerContainerRuntime()

    def _inspect(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            args=["docker", "inspect"],
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "State": {
                            "Status": "exited",
                            "Running": False,
                            "ExitCode": 2,
                            "OOMKilled": False,
                            "Error": "",
                        }
                    }
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _inspect)

    inspected = runtime.inspect_job(_record(paths))

    assert inspected["status"] == "failed"
    assert inspected["exit_code"] == 2
    assert inspected["oom_killed"] is False
    assert inspected["runtime_error"] is None


def test_docker_runtime_reports_causal_pids_limit_exhaustion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    (proc_root / "123").mkdir(parents=True)
    (proc_root / "123" / "cgroup").write_text(
        "0::/system.slice/docker-container-1.scope\n",
        encoding="utf-8",
    )
    events_path = cgroup_root / "system.slice" / "docker-container-1.scope" / "pids.events"
    events_path.parent.mkdir(parents=True)
    events_path.write_text("max 0\n", encoding="utf-8")
    runtime = DockerContainerRuntime(proc_root=proc_root, cgroup_root=cgroup_root)
    calls: list[list[str]] = []

    def _run(args, **kwargs):
        del kwargs
        calls.append(args)
        if args[:3] == ["docker", "run", "-d"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="container-1\n")
        if "--format" in args:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="123\n")
        if args[-3:] == ["ps", "-eo", "pid=,ppid=,pgid=,args="]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="675 1 675 dotnet test Src/App.Tests.csproj\n",
            )
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "State": {
                            "Status": "exited",
                            "Running": False,
                            "ExitCode": 1,
                            "OOMKilled": False,
                            "Error": "",
                        }
                    }
                ]
            ),
        )

    monkeypatch.setattr(subprocess, "run", _run)
    record = _record(paths).model_copy(update={"container_id": "container-1"})

    runtime.start_job(_request(), record, paths)
    events_path.write_text("max 1\n", encoding="utf-8")
    deadline = time.monotonic() + 1.0
    while not runtime._pids_exhaustion_marker(record).exists() and time.monotonic() < deadline:  # noqa: SLF001
        time.sleep(0.01)
    inspected = runtime.inspect_job(record)

    assert inspected["failure_cause"] == "pids_limit_exhausted"
    assert inspected["oom_killed"] is False
    assert ["docker", "exec", "container-1", "kill", "-TERM", "-675"] in calls


def test_docker_runtime_interrupts_only_a_recognized_test_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    record = _record(paths).model_copy(update={"container_id": "container-1"})
    calls: list[list[str]] = []

    def _run(args, **kwargs):
        del kwargs
        calls.append(args)
        if args[-3:] == ["ps", "-eo", "pid=,ppid=,pgid=,args="]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=(
                    "1 0 1 /bin/bash /run-agent.sh\n"
                    "205 1 205 /usr/local/bin/codex-code-mode-host codex exec dotnet test\n"
                    "675 1 675 timeout --preserve-status 300 dotnet test Src/App.Tests.csproj\n"
                    "676 675 675 dotnet test Src/App.Tests.csproj\n"
                ),
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", _run)

    result = DockerContainerRuntime().interrupt_active_tool(record, reason="operator_requested")

    assert result["interrupted"] is True
    assert result["process_group_id"] == 675
    assert calls[-1] == ["docker", "exec", "container-1", "kill", "-TERM", "-675"]
    assert not any("205" in " ".join(call) and "kill" in call for call in calls)


def test_docker_runtime_does_not_interrupt_codex_or_unrecognized_processes() -> None:
    process_table = (
        "205 1 205 /usr/local/bin/codex-code-mode-host codex exec dotnet test\n"
        "400 1 400 bash -lc sleep 999\n"
    )

    assert DockerContainerRuntime._test_process_group(process_table) is None  # noqa: SLF001


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


def test_subscription_app_server_gets_only_restricted_proxy_configuration(
    tmp_path: Path,
) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    runtime = DockerContainerRuntime(
        docker_network="agent-worker-net",
        codex_egress_proxy_url="http://172.18.0.1:11889",
    )
    turn = CodexTurnSpec(
        auth_mode=CodexAuthMode.CHATGPT_SUBSCRIPTION,
        prompt="continue",
        model="gpt-5.4",
        mcp_gateway_url="http://10.20.30.40:8000/mcp",
    )
    request = _request().model_copy(update={"codex_turn": turn})

    run_args = runtime._build_run_args(  # noqa: SLF001
        request=request,
        record=_record(paths),
        paths=paths,
    )
    app_server_args = list(runtime.codex_app_server_command(_record(paths), turn))

    assert not any("access_token" in value.casefold() for value in run_args + app_server_args)
    assert not any("auth.json" in value for value in run_args + app_server_args)
    assert not any("OPENAI_API_KEY" in value for value in app_server_args)
    assert not any(value.startswith("HTTPS_PROXY=") for value in run_args)
    assert f"{paths.logs}/codex-turns:/run/evander/codex-turns:ro" in run_args
    assert "/home/agent/.evander/codex-turns" not in " ".join(run_args)
    subscription_config = paths.root / "codex-subscription-config.toml"
    subscription_project_config = paths.root / "codex-subscription-project-config.toml"
    assert f"{subscription_config}:/home/agent/.config/evander/codex/config.toml:ro" in run_args
    assert f"{subscription_project_config}:/home/agent/.codex/config.toml:ro" in run_args
    subscription_config_text = subscription_config.read_text(encoding="utf-8")
    assert 'forced_login_method = "chatgpt"' in subscription_config_text
    assert 'model_reasoning_effort = "high"' in subscription_config_text
    assert "[mcp_servers.evander]" in subscription_config_text
    assert 'url = "http://10.20.30.40:8000/mcp"' in subscription_config_text
    assert "enabled = true" in subscription_config_text
    assert "required = true" in subscription_config_text
    assert "model_provider" not in subscription_config_text
    assert "model_providers" not in subscription_config_text
    assert "OPENAI_API_KEY" not in subscription_config_text
    assert "model_provider" not in subscription_project_config.read_text(encoding="utf-8")
    assert app_server_args[-3:] == ["/usr/local/bin/codex", "app-server", "--stdio"]
    assert "/home/agent/bin/codex" not in app_server_args
    assert not any(str(turn.mcp_gateway_url) in value for value in app_server_args)
    assert "CODEX_HOME=/home/agent/.config/evander/codex" in app_server_args
    assert "HTTPS_PROXY=http://172.18.0.1:11889" in app_server_args
    no_proxy = next(value for value in app_server_args if value.startswith("NO_PROXY="))
    assert "10.20.30.40" in no_proxy


def test_subscription_container_requires_restricted_proxy(tmp_path: Path) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    runtime = DockerContainerRuntime(docker_network="agent-worker-net")
    request = _request().model_copy(
        update={
            "codex_turn": CodexTurnSpec(
                auth_mode=CodexAuthMode.CHATGPT_SUBSCRIPTION,
                prompt="continue",
                model="gpt-5.4",
            )
        }
    )

    with pytest.raises(ValueError, match="restricted codex_egress_proxy_url"):
        runtime._build_run_args(  # noqa: SLF001
            request=request,
            record=_record(paths),
            paths=paths,
        )


def test_docker_runtime_adds_worker_read_only_mounts(tmp_path: Path) -> None:
    paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    runtime = DockerContainerRuntime(
        docker_network="none",
        read_only_mounts=[
            "/cache/dotnet:/opt/evander/toolchains/dotnet",
            "/cache/node:/opt/evander/toolchains/node-v24.14.0:ro",
        ],
    )

    args = runtime._build_run_args(request=_request(), record=_record(paths), paths=paths)  # noqa: SLF001

    assert "-v" in args
    assert "/cache/dotnet:/opt/evander/toolchains/dotnet:ro" in args
    assert "/cache/node:/opt/evander/toolchains/node-v24.14.0:ro" in args


def test_session_pins_bundle_across_selector_switch_and_daemon_restart(tmp_path: Path) -> None:
    first, second = tmp_path / "bundle-1", tmp_path / "bundle-2"
    for bundle in (first, second):
        (bundle / "sdk").mkdir(parents=True)
        (bundle / "tools").mkdir()
    selector = tmp_path / "dotnet-current"
    selector.symlink_to(first)
    mounts = [f"{selector}/sdk:/opt/sdk", f"{selector}/tools:/opt/tools"]
    runtime = DockerContainerRuntime(read_only_mounts=mounts)
    paths = WorkerSessionPaths.resolve(tmp_path / "worker", "session-1")
    record = _record(paths)
    args = runtime._build_run_args(request=_request(), record=record, paths=paths)
    assert f"{first}/sdk:/opt/sdk:ro" in args
    replacement = tmp_path / "next"
    replacement.symlink_to(second)
    replacement.replace(selector)
    runtime = DockerContainerRuntime(read_only_mounts=mounts)
    same_session = runtime._build_run_args(request=_request(), record=record, paths=paths)
    assert f"{first}/sdk:/opt/sdk:ro" in same_session
    assert record.metadata["read_only_mounts"][1]["source"] == str(first / "tools")
    new_paths = WorkerSessionPaths.resolve(tmp_path / "worker", "session-2")
    assert runtime._session_read_only_mounts(new_paths)[0].host_path == str(second / "sdk")
    (first / "sdk").rmdir()
    with pytest.raises(ValueError, match="missing"):
        runtime._session_read_only_mounts(paths)


def test_related_mounts_resolve_the_selector_once(tmp_path: Path, monkeypatch) -> None:
    first, second = tmp_path / "bundle-1", tmp_path / "bundle-2"
    for bundle in (first, second):
        (bundle / "sdk").mkdir(parents=True)
        (bundle / "tools").mkdir()
    selector = tmp_path / "current"
    selector.symlink_to(first)
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == selector:
            selector.unlink()
            selector.symlink_to(second)
        return result
    monkeypatch.setattr(Path, "resolve", resolve)
    runtime = DockerContainerRuntime(read_only_mounts=[f"{selector}/sdk:/opt/sdk", f"{selector}/tools:/opt/tools"])
    paths = WorkerSessionPaths.resolve(tmp_path / "worker", "session-1")
    resolved = runtime._session_read_only_mounts(paths)
    assert [mount.host_path for mount in resolved] == [str(first / "sdk"), str(first / "tools")]


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
    assert "agent:x:1001:1001:Evander:/home/agent:/bin/bash" in passwd_path.read_text(
        encoding="utf-8"
    )
    assert "agent:x:1001:" in group_path.read_text(encoding="utf-8")
    assert args[args.index("--user") + 1] == "1001:1001"


def test_docker_runtime_rejects_session_paths_outside_sessions_root_before_mount(
    tmp_path: Path,
) -> None:
    safe_paths = WorkerSessionPaths.resolve(tmp_path / "worker-root", "session-1")
    unsafe_root = safe_paths.sessions_root / ".."
    unsafe_paths = WorkerSessionPaths(
        session_id="session-1",
        sessions_root=safe_paths.sessions_root,
        root=unsafe_root,
        agent_home=unsafe_root / "agent_home",
        repo_root=unsafe_root / "agent_home" / "repos",
        artifacts=unsafe_root / "artifacts",
        logs=unsafe_root / "logs",
        metadata_path=unsafe_root / "metadata.json",
    )
    runtime = DockerContainerRuntime()

    with pytest.raises(ValueError, match="session root"):
        runtime._build_run_args(  # noqa: SLF001
            request=_request(),
            record=_record(safe_paths),
            paths=unsafe_paths,
        )


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
    assert args[args.index("--tmpfs") + 1] == "/tmp:rw,noexec,nosuid,size=4g"
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
        sessions_root=paths.sessions_root,
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
