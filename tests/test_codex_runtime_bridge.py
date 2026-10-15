import json
import socket
import time

import pytest

from evander.codex_auth import CodexRuntimeSpec
from evander.worker.codex_auth_broker import CodexExternalAuthTokens
from evander.worker.codex_runtime_bridge import (
    CodexRuntimeBridge,
    parse_arguments,
    validate_request,
)
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
    ],
)
def test_runtime_cannot_override_broker_authentication(arg):
    with pytest.raises(ValueError):
        parse_arguments(["exec", "-c", arg, "scan"], "/home/agent")


class NativeProcess:
    def __init__(self, command, *, notification_handler, server_request_handlers, **kwargs):
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
        if method == "thread/start":
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
