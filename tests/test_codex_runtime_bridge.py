import json
import socket
import time

import pytest

from evander.codex_auth import CodexRuntimeSpec
from evander.worker.codex_auth_broker import CodexExternalAuthTokens
from evander.worker.codex_runtime_bridge import (
    CodexRuntimeBridge,
    parse_arguments,
    preflight_request,
    validate_request,
)
from evander.worker.codex_runtime_policy import RUNTIME_REQUIRED_VALUES
from evander.worker.models import WorkerJobRequest


def test_runtime_request_does_not_allow_alternate_host_paths_or_environment():
    for header in [
        {"argv": ["exec", "prompt"], "cwd": "/etc"},
        {"argv": ["exec", "prompt"], "cwd": "/home/agent/../other"},
        {
            "argv": ["exec", "prompt"],
            "cwd": "/home/agent",
            "environment": {"OPENAI_API_KEY": "secret"},
        },
    ]:
        with pytest.raises(ValueError):
            validate_request(header)


def test_deep_scan_cli_arguments_preserve_permission_profiles():
    mode, cwd, config, model, prompt = parse_arguments(
        [
            "exec",
            "--experimental-json",
            "--thread-source",
            "security_scan",
            "--config",
            'default_permissions="codex_security_deep_scan_worker"',
            "--config",
            'permissions.codex_security_deep_scan_worker.filesystem={":root"="read"}',
            "--cd",
            "/artifacts/security-scans/work",
            "--model",
            "test-model",
            "scan this",
        ],
        "/home/agent",
    )
    assert mode == "exec" and prompt == "scan this"
    assert model == "test-model" and cwd == "/artifacts/security-scans/work"
    assert config["permissions"]["codex_security_deep_scan_worker"]["filesystem"][":root"] == "read"


@pytest.mark.parametrize(
    "arg",
    [
        'model_provider="another"',
        'model_providers.evil.base_url="https://invalid.example"',
        'forced_login_method="api"',
        'chatgpt_base_url="http://127.0.0.1:8080"',
        'openai_base_url="http://127.0.0.1:8080"',
        'experimental_thread_store_endpoint="http://127.0.0.1:8080"',
        'features={code_mode_host={url="http://127.0.0.1:8080"}}',
        'agents.scan.config_file="/home/agent/untrusted.toml"',
        'permissions={scan={filesystem={":root"="read"}}}\nchatgpt_base_url="http://localhost"',
    ],
)
def test_runtime_cannot_override_broker_authentication(arg):
    with pytest.raises(ValueError):
        parse_arguments(["exec", "-c", arg, "scan"], "/home/agent")


class NativeProcess:
    def __init__(self, command, *, notification_handler, server_request_handlers, **kwargs):
        self.command = command
        self.notify_handler = notification_handler
        self.handlers = server_request_handlers
        self.calls = []
        self.running = False
        self.initialization_result = {"userAgent": "native-test"}

    def start(self):
        self.running = True

    def close(self):
        self.running = False

    def notify(self, *args):
        pass

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "configRequirements/read":
            return {"requirements": dict(RUNTIME_REQUIRED_VALUES)}
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": "thread-test"}}
        if method == "turn/start":
            self.notify_handler(
                "item/completed", {"item": {"id": "a", "type": "agentMessage", "text": "done"}}
            )
            self.notify_handler("turn/completed", {"turn": {"status": "completed"}})
            return {}
        return {"ok": True}


def test_broker_emits_sdk_events_but_keeps_tokens_on_native_side(tmp_path):
    native = []
    tokens = CodexExternalAuthTokens("private-test-token", "account-1", "test")

    def factory(*args, **kwargs):
        value = NativeProcess(*args, **kwargs)
        native.append(value)
        return value

    bridge = CodexRuntimeBridge(
        path=tmp_path / "broker.sock",
        command_factory=lambda env, args: ["native"],
        tokens=tokens,
        refresh_tokens=lambda **kwargs: tokens,
        process_factory=factory,
    )
    bridge.start()
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3)
            client.connect(str(bridge.path))
            client.sendall(
                (
                    json.dumps({"argv": ["exec", "--json", "scan"], "cwd": "/home/agent"}) + "\n"
                ).encode()
            )
            frames = []
            with client.makefile("rb") as stream:
                for line in stream:
                    frames.append(json.loads(line))
                    if "exit_code" in frames[-1]:
                        break
        assert frames[-1] == {"exit_code": 0}
        events = [json.loads(frame["text"]) for frame in frames if frame.get("stream") == "stdout"]
        assert [event["type"] for event in events] == [
            "thread.started",
            "item.completed",
            "turn.completed",
        ]
        assert "private-test-token" not in json.dumps(frames)
        login = next(
            params for method, params in native[0].calls if method == "account/login/start"
        )
        assert login["accessToken"] == "private-test-token"
    finally:
        bridge.close()


def test_runtime_preflight_cannot_read_or_replace_credentials(tmp_path):
    processes = []
    tokens = CodexExternalAuthTokens("private-token", "account-1", None)

    def factory(*args, **kwargs):
        process = NativeProcess(*args, **kwargs)
        processes.append(process)
        return process

    bridge = CodexRuntimeBridge(
        path=tmp_path / "bridge.sock",
        command_factory=lambda env, args: [],
        tokens=tokens,
        refresh_tokens=lambda **kwargs: tokens,
        process_factory=factory,
    )
    bridge.start()
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3)
            client.connect(str(bridge.path))
            client.sendall(
                (
                    json.dumps({"argv": ["app-server", "--stdio"], "cwd": "/home/agent"}) + "\n"
                ).encode()
            )
            client.sendall(b'{"id":1,"method":"initialize","params":{}}\n')
            stream = client.makefile("rb")
            first = json.loads(stream.readline())
            assert json.loads(first["text"])["result"] == {"userAgent": "native-test"}
            client.sendall(b'{"id":2,"method":"account/login/start","params":{}}\n')
            error = json.loads(stream.readline())
            assert error["stream"] == "stderr"
            assert "private-token" not in error["text"]
        assert len([call for call in processes[0].calls if call[0] == "account/login/start"]) == 1
    finally:
        bridge.close()


def test_job_wire_contract_forbids_two_authentication_modes():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="mutually exclusive"):
        WorkerJobRequest(
            session_id="session-1",
            image="runner",
            command=["scan"],
            codex_runtime=CodexRuntimeSpec(profile_id="scan"),
            codex_turn={"prompt": "scan", "model": "test"},
        )


@pytest.mark.parametrize("same_account", [True, False])
def test_runtime_refresh_is_pinned_to_selected_account(tmp_path, same_account):
    refreshes = []
    initial = CodexExternalAuthTokens("initial-private-token", "account-1", None)

    class RefreshingNative(NativeProcess):
        def request(self, method, params):
            if method == "thread/start":
                self.handlers["account/chatgptAuthTokens/refresh"](
                    {"previousAccountId": "account-1"}
                )
            return super().request(method, params)

    def refresh(**kwargs):
        refreshes.append(kwargs)
        return CodexExternalAuthTokens(
            "replacement-private-token", "account-1" if same_account else "other-account", None
        )

    bridge = CodexRuntimeBridge(
        path=tmp_path / "broker.sock",
        command_factory=lambda env, args: ["native"],
        tokens=initial,
        refresh_tokens=refresh,
        process_factory=RefreshingNative,
    )
    bridge.start()
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3)
            client.connect(str(bridge.path))
            client.sendall(
                (json.dumps({"argv": ["exec", "scan"], "cwd": "/home/agent"}) + "\n").encode()
            )
            frames = []
            with client.makefile("rb") as stream:
                for line in stream:
                    frames.append(json.loads(line))
                    if "exit_code" in frames[-1]:
                        break
        assert frames[-1]["exit_code"] == (0 if same_account else 1)
        assert refreshes == [
            {"previous_access_token": "initial-private-token", "previous_account_id": "account-1"}
        ]
        assert "private-token" not in json.dumps(frames)
    finally:
        bridge.close()


def test_runtime_bounds_clients_before_they_supply_a_command(tmp_path):
    bridge = CodexRuntimeBridge(
        path=tmp_path / "broker.sock",
        command_factory=lambda env, args: ["native"],
        tokens=CodexExternalAuthTokens("test-token", "account-1", None),
        refresh_tokens=lambda **kwargs: None,
        max_processes=1,
        process_factory=NativeProcess,
    )
    bridge.start()
    first = socket.socket(socket.AF_UNIX)
    try:
        first.connect(str(bridge.path))
        deadline = time.monotonic() + 3
        while not bridge.clients and time.monotonic() < deadline:
            time.sleep(0.01)
        assert bridge.clients
        with socket.socket(socket.AF_UNIX) as second:
            second.settimeout(3)
            second.connect(str(bridge.path))
            with second.makefile("rb") as stream:
                assert json.loads(stream.readline())["stream"] == "stderr"
                assert json.loads(stream.readline()) == {"exit_code": 1}
    finally:
        first.close()
        bridge.close()


def exchange(bridge, header, *messages):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        client.connect(str(bridge.path))
        client.sendall((json.dumps(header) + "\n").encode())
        for message in messages:
            client.sendall((json.dumps(message) + "\n").encode())
        client.shutdown(socket.SHUT_WR)
        with client.makefile("rb") as stream:
            return [json.loads(line) for line in stream]


@pytest.fixture
def broker(tmp_path):
    processes = []

    def factory(*args, **kwargs):
        process = NativeProcess(*args, **kwargs)
        processes.append(process)
        return process

    tokens = CodexExternalAuthTokens("private-token", "account-1", None)
    bridge = CodexRuntimeBridge(
        path=tmp_path / "broker.sock",
        command_factory=lambda env, args: ["native", *args],
        tokens=tokens,
        refresh_tokens=lambda **kwargs: tokens,
        process_factory=factory,
    )
    bridge.start()
    try:
        yield bridge, processes
    finally:
        bridge.close()


@pytest.mark.parametrize(
    "method,params",
    [
        ("thread/start", {"config": {"chatgpt_base_url": "http://127.0.0.1"}}),
        ("thread/resume", {"config": {"modelProvider": "evil"}}),
        ("thread/fork", {"config": {"openai_base_url": "http://127.0.0.1"}}),
        ("turn/start", {"threadId": "test"}),
        ("config/value/write", {"keyPath": "chatgpt_base_url", "value": "http://127.0.0.1"}),
        ("config/batchWrite", {"edits": []}),
        ("fs/writeFile", {"path": "/home/agent/config.toml", "data": "untrusted"}),
        ("externalAgentConfig/import", {}),
        ("command/exec", {"command": ["untrusted"]}),
        ("config/mcpServer/reload", {}),
        ("account/read", {"refreshToken": True}),
        ("config/read", {"include_layers": True}),
        ("config/read", {"config": {"chatgpt_base_url": "http://127.0.0.1"}}),
    ],
)
def test_raw_runtime_rejects_mutations_and_unknown_parameter_aliases(broker, method, params):
    bridge, processes = broker
    frames = exchange(
        bridge,
        {"argv": ["app-server"], "cwd": "/home/agent"},
        {"id": 10, "method": method, "params": params},
    )
    assert frames[-1] == {"exit_code": 1}
    assert len(processes[0].calls) == 2  # Policy read and worker-owned login only.
    assert "private-token" not in json.dumps(frames)


@pytest.mark.parametrize("params", [{}, {"refreshToken": False}])
def test_raw_runtime_preserves_read_only_sdk_preflight(broker, params):
    bridge, processes = broker
    calls = [
        ("initialize", {"clientInfo": {"name": "sdk"}}),
        ("config/read", {"cwd": "/home/agent/repo", "includeLayers": False}),
        ("permissionProfile/list", {"cwd": "/home/agent/repo", "cursor": None}),
        ("configRequirements/read", {}),
        ("account/read", params),
    ]
    frames = exchange(
        bridge,
        {"argv": ["app-server"], "cwd": "/home/agent"},
        *[
            {"id": index, "method": method, "params": value}
            for index, (method, value) in enumerate(calls)
        ],
    )
    assert len(frames) == len(calls)
    assert [json.loads(frame["text"])["id"] for frame in frames] == list(range(len(calls)))
    assert processes[0].calls[2:] == calls[1:]


def test_runtime_never_sends_tokens_to_native_without_managed_policy(broker):
    bridge, processes = broker

    class OldNative(NativeProcess):
        def request(self, method, params):
            self.calls.append((method, params))
            return {"requirements": {}}

    def factory(*args, **kwargs):
        value = OldNative(*args, **kwargs)
        processes.append(value)
        return value

    bridge.process_factory = factory
    frames = exchange(bridge, {"argv": ["exec", "scan"], "cwd": "/home/agent"})
    assert frames[-1] == {"exit_code": 1}
    assert processes[0].calls == [("configRequirements/read", {})]
    assert "private-token" not in json.dumps(frames)


def test_sdk_resume_preserves_permissions_and_discards_auth_store_default(broker):
    bridge, processes = broker
    frames = exchange(
        bridge,
        {
            "argv": [
                "exec",
                "--experimental-json",
                "--config",
                'cli_auth_credentials_store="auto"',
                "--config",
                'default_permissions="scanner"',
                "--config",
                'permissions.scanner={extends=":read-only"}',
                "resume",
                "existing-thread",
                "continue scan",
            ],
            "cwd": "/home/agent",
        },
    )
    assert frames[-1] == {"exit_code": 0}
    resumed = next(params for method, params in processes[0].calls if method == "thread/resume")
    assert resumed["threadId"] == "existing-thread"
    assert resumed["permissions"] == "scanner"
    assert resumed["config"]["permissions"]["scanner"] == {"extends": ":read-only"}
    assert "cli_auth_credentials_store" not in resumed["config"]
    assert all("cli_auth_credentials_store" not in arg for arg in processes[0].command)


def test_preflight_cannot_read_configuration_outside_session():
    with pytest.raises(ValueError):
        preflight_request("config/read", {"cwd": "/etc"})
