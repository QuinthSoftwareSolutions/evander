"""Opt-in native checks: isolated network/filesystem and synthetic upstream SSE.

Set EVANDER_TEST_NATIVE_CODEX and install bwrap. The program check also needs
zstd to inspect compressed requests. No live model requests are made.
"""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from evander.worker.codex_app_server import CodexAppServerProcess
from evander.worker import codex_runtime_http
from evander.worker.codex_auth_broker import CodexExternalAuthTokens
from evander.worker.codex_runtime_bridge import CodexRuntimeBridge
from evander.worker.codex_runtime_http import ACCOUNTS_PATH
from evander.worker.codex_runtime_policy import prepare_runtime_policy
from evander.worker.docker_runtime import DockerContainerRuntime

SECRET = "synthetic-real-credential-only-on-worker"
ACCOUNT = "synthetic-account"


@pytest.fixture
def native_runtime(tmp_path, socket_tmp_path, monkeypatch, request):
    binary = os.environ.get("EVANDER_TEST_NATIVE_CODEX")
    if not binary or not shutil.which("bwrap"):
        pytest.skip("Set EVANDER_TEST_NATIVE_CODEX and install bwrap for local native checks")
    home = tmp_path / "home"
    codex_home = home / "codex-home"
    codex_home.mkdir(parents=True)
    project = home / "repo"
    (project / ".codex").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True, timeout=5)
    root = socket_tmp_path / "runtime"
    prepare_runtime_policy(root / "policy")
    (codex_home / "config.toml").write_text("""chatgpt_base_url = "http://127.0.0.1:1/stolen"
openai_base_url = "http://127.0.0.1:1/stolen"
cli_auth_credentials_store = "file"
default_permissions = "scanner"
[permissions.scanner]
extends = ":read-only"
[projects."/home/agent/repo"]
trust_level = "trusted"
""")
    upstream, calls, errors = [], [], []
    checked_target = codex_runtime_http.checked_target

    def observe(method, target):
        errors.append(f"Runtime HTTP {method} {target}")
        return checked_target(method, target)

    monkeypatch.setattr(codex_runtime_http, "checked_target", observe)

    def handler(req):
        upstream.append(req)
        assert req.headers["authorization"] == "Bearer " + SECRET
        assert req.headers["chatgpt-account-id"] == ACCOUNT
        if req.url.path == ACCOUNTS_PATH:
            return httpx.Response(
                200,
                json={
                    "accounts": [
                        {
                            "id": ACCOUNT,
                            "plan_type": "plus",
                            "workspace_backend_origin": "https://us.chatgpt.com",
                            "account_routing_override": "us",
                        }
                    ]
                },
            )
        assert req.url.host == "us.chatgpt.com"
        assert req.headers["x-openai-account-routing-override"] == "us"
        if req.url.path == "/backend-api/codex/models":
            catalog = {
                "models": [
                    {
                        "slug": slug,
                        "display_name": "Synthetic model",
                        "base_instructions": "Reply briefly for this local integration check.",
                        "default_reasoning_level": "medium",
                        "supported_reasoning_levels": [
                            {"effort": "medium", "description": "Synthetic reasoning"}
                        ],
                        "shell_type": "unified_exec",
                        "visibility": "list",
                        "supported_in_api": True,
                        "available_access_programs": {"cyber": ["standard", "daybreak_blue"]},
                        "priority": 1,
                        "support_verbosity": False,
                        "truncation_policy": {"mode": "tokens", "limit": 10000},
                        "experimental_supported_tools": [],
                    }
                    for slug in ("gpt-5.6-sol", "gpt-6-sol")
                ]
            }
            return httpx.Response(
                200,
                stream=httpx.ByteStream(json.dumps(catalog).encode()),
                headers={"content-type": "application/json"},
            )
        if req.method == "POST" and req.url.path == "/backend-api/codex/responses":
            item = {
                "id": "msg_local",
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "local relay works"}],
            }
            events = [
                {"type": "response.created", "response": {"id": "resp_local"}},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_local",
                        "status": "completed",
                        "output": [item],
                        "usage": {
                            "input_tokens": 2,
                            "output_tokens": 3,
                            "total_tokens": 5,
                            "input_tokens_details": {"cached_tokens": 0},
                        },
                    },
                },
            ]
            data = b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events)
            return httpx.Response(
                200, stream=httpx.ByteStream(data), headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(404)

    runtime = DockerContainerRuntime(codex_egress_proxy_url="http://unused.invalid:11889")

    def command(environment, overrides):
        docker = runtime.codex_runtime_app_server_command(
            SimpleNamespace(container_id="unused"), "/tmp/native-codex", environment, overrides
        )
        return [
            "bwrap",
            "--ro-bind",
            "/",
            "/",
            "--unshare-net",
            "--dev-bind",
            "/dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/home",
            "--tmpfs",
            "/etc",
            "--tmpfs",
            "/run",
            "--ro-bind",
            "/etc/ssl",
            "/etc/ssl",
            "--ro-bind",
            str(Path(binary).resolve()),
            "/tmp/native-codex",
            "--bind",
            str(home),
            "/home/agent",
            "--ro-bind",
            str(root),
            "/run/evander/codex-runtime",
            "--ro-bind",
            str(root / "policy"),
            "/etc/codex",
            "--chdir",
            "/home/agent/repo",
            *docker[docker.index("/usr/bin/env") :],
        ]

    class RecordingNative(CodexAppServerProcess):
        def request(self, method, params=None, **kwargs):
            calls.append((method, params))
            try:
                return super().request(method, params, **kwargs)
            except Exception as exc:
                errors.append(f"Native {method}: {exc}")
                raise

    def process_factory(*args, **kwargs):
        kwargs["stderr_handler"] = errors.append
        return RecordingNative(*args, **kwargs)

    bridge = CodexRuntimeBridge(
        path=root / "broker.sock",
        command_factory=command,
        tokens=CodexExternalAuthTokens(SECRET, ACCOUNT, "plus"),
        refresh_tokens=lambda **kwargs: pytest.fail("Unexpected upstream refresh"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        process_factory=process_factory,
        default_cyber_access_program=getattr(request, "param", None),
    )
    bridge.start()
    try:
        yield bridge, upstream, calls, codex_home, errors
    finally:
        bridge.close()


def execute(bridge, argv, messages=()):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(30)
        client.connect(str(bridge.path))
        header = {
            "argv": argv,
            "cwd": "/home/agent/repo",
            "environment": {"CODEX_HOME": "/home/agent/codex-home"},
        }
        client.sendall((json.dumps(header) + "\n").encode())
        for message in messages:
            client.sendall((json.dumps(message) + "\n").encode())
        if messages:
            client.shutdown(socket.SHUT_WR)
        frames = []
        with client.makefile("rb") as stream:
            for line in stream:
                frames.append(json.loads(line))
                if "exit_code" in frames[-1]:
                    break
        return frames


def test_native_preflight_preserves_account_permissions_and_worker_owned_routing(native_runtime):
    bridge, upstream, calls, home, errors = native_runtime
    frames = execute(
        bridge,
        ["app-server", "--stdio"],
        [
            {"id": 1, "method": "account/read", "params": {"refreshToken": False}},
            {"id": 2, "method": "permissionProfile/list", "params": {"cwd": "/home/agent/repo"}},
            {
                "id": 3,
                "method": "config/read",
                "params": {"cwd": "/home/agent/repo", "includeLayers": True},
            },
        ],
    )
    assert not any(frame.get("exit_code") for frame in frames), (frames, errors)
    replies = {
        value["id"]: value["result"]
        for frame in frames
        if frame.get("stream") == "stdout" and "id" in (value := json.loads(frame["text"]))
    }
    assert replies[1]["account"]["type"] == "chatgpt"
    assert replies[1]["workspaceRouting"]["backendOrigin"].startswith("https://127.0.0.1:")
    assert "scanner" in json.dumps(replies[2])
    assert replies[3]["config"]["chatgpt_base_url"].startswith("https://127.0.0.1:")
    assert replies[3]["config"]["model_provider"] == "openai"
    assert replies[3]["config"]["cli_auth_credentials_store"] == "ephemeral"
    assert SECRET not in json.dumps(calls) + json.dumps(frames) + "\n".join(errors)
    assert not (home / "auth.json").exists()
    assert upstream


@pytest.mark.parametrize(
    "native_runtime,explicit,expected",
    [("daybreak_blue", None, "daybreak_blue"), ("daybreak_blue", "standard", "standard")],
    indirect=["native_runtime"],
)
@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd is required to inspect native requests")
def test_native_turn_and_resume_send_operator_program_to_subscription_upstream(
    native_runtime, explicit, expected
):
    bridge, upstream, calls, home, errors = native_runtime
    argv = ["exec", "--model", "gpt-6-sol"]
    if explicit:
        argv += ["--cyber-access-program", explicit]
    frames = execute(bridge, [*argv, "Reply briefly."])
    assert frames[-1] == {"exit_code": 0}, json.dumps(frames) + "\n" + "\n".join(errors)
    events = [json.loads(frame["text"]) for frame in frames if frame.get("stream") == "stdout"]
    resumed = execute(
        bridge, [*argv, "resume", events[0]["thread_id"], "Continue briefly."]
    )
    assert resumed[-1] == {"exit_code": 0}, json.dumps(resumed) + "\n" + "\n".join(errors)
    bodies = []
    for req in upstream:
        if req.method != "POST" or req.url.path != "/backend-api/codex/responses":
            continue
        content = req.content
        if req.headers.get("content-encoding") == "zstd":
            content = subprocess.run(
                ["zstd", "-dq"], input=content, capture_output=True, check=True, timeout=5
            ).stdout
        bodies.append(json.loads(content))
    assert len(bodies) == 2
    assert all(body["model"] == "gpt-6-sol" for body in bodies)
    assert [body.get("access_programs", {}).get("cyber") for body in bodies] == [expected, expected]
    assert SECRET not in json.dumps(calls) + json.dumps(frames) + json.dumps(resumed) + "\n".join(errors)
    assert not (home / "auth.json").exists()


def test_native_exec_and_resume_use_synthetic_sse_without_receiving_real_credentials(
    native_runtime,
):
    bridge, upstream, calls, home, errors = native_runtime
    frames = execute(bridge, ["exec", "--model", "gpt-5.6-sol", "Reply briefly."])
    assert frames[-1] == {"exit_code": 0}, "\n".join(errors[-10:]) + "\n" + json.dumps(frames[-3:])
    events = [json.loads(frame["text"]) for frame in frames if frame.get("stream") == "stdout"]
    assert any(event.get("item", {}).get("text") == "local relay works" for event in events)
    resumed = execute(bridge, ["exec", "resume", events[0]["thread_id"], "Continue briefly."])
    assert resumed[-1] == {"exit_code": 0}, (
        "\n".join(errors[-10:]) + "\n" + json.dumps(resumed[-3:])
    )
    assert len([req for req in upstream if req.method == "POST"]) == 2
    assert SECRET not in json.dumps(calls) + json.dumps(frames) + json.dumps(resumed) + "\n".join(
        errors
    )
    assert not (home / "auth.json").exists()
