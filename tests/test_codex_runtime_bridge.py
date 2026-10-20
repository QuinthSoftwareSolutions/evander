import json
import os
from pathlib import Path
import socket
import threading
from types import SimpleNamespace
import time

import httpx
import pytest

from evander.codex_auth import CodexRuntimeSpec
from evander.worker.codex_auth_broker import CodexExternalAuthTokens
from evander.worker.codex_runtime_bridge import (
    CodexRuntimeBridge,
    load_runtime_profiles,
    parse_arguments,
    preflight_request,
    validate_request,
)
from evander.worker.codex_runtime_policy import RUNTIME_REQUIRED_VALUES
from evander.worker.codex_runtime_transport import UnixConnection
from evander.worker.models import WorkerJobRequest


def unused_http_client():
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected upstream request"))
    )


def test_private_diagnostics_keep_child_tools_and_failure_details_out_of_exec_frames(tmp_path):
    from evander.worker.private_diagnostics import PrivateJobDiagnostics

    store = PrivateJobDiagnostics(tmp_path / "private")
    capture = store.begin("scan-1")

    class DiagnosticProcess(NativeProcess):
        def __init__(self, *args, stderr_handler, **kwargs):
            super().__init__(*args, **kwargs)
            self.stderr = stderr_handler

        def request(self, method, params):
            if method == "turn/start":
                self.stderr("private native warning")
                self.notify_handler(
                    "item/completed",
                    {
                        "threadId": "child-thread",
                        "item": {
                            "type": "commandExecution",
                            "aggregatedOutput": "private tool output",
                        },
                    },
                )
                self.notify_handler(
                    "thread/tokenUsage/updated",
                    {"threadId": "child-thread", "tokenUsage": {"modelContextWindow": 128000}},
                )
                self.notify_handler(
                    "turn/completed", {"threadId": "child-thread", "turn": {"status": "completed"}}
                )
                self.notify_handler(
                    "turn/completed",
                    {
                        "threadId": "thread-test",
                        "turn": {
                            "status": "failed",
                            "error": {"message": "private terminal detail"},
                        },
                    },
                )
                return {}
            return super().request(method, params)

    tokens = CodexExternalAuthTokens("private-subscription-token", "account-1", "test")
    bridge = CodexRuntimeBridge(
        path=tmp_path / "broker.sock",
        command_factory=lambda env, args: ["native"],
        tokens=tokens,
        refresh_tokens=lambda **kwargs: tokens,
        http_client=unused_http_client(),
        process_factory=DiagnosticProcess,
        diagnostics=capture,
        default_cyber_access_program="daybreak_blue",
    )
    bridge.start()
    try:
        frames = exchange(bridge, {"argv": ["exec", "scan"], "cwd": "/home/agent"})
        assert frames[-1] == {"exit_code": 1}
        public = json.dumps(frames)
        assert "private tool output" not in public and "private native warning" not in public
        assert "private terminal detail" not in public
        private = (capture.directory / "events.jsonl").read_text()
        assert "private tool output" in private and "private native warning" in private
        assert "private terminal detail" in private and "modelContextWindow" in private
        started = next(
            event
            for line in private.splitlines()
            if (event := json.loads(line))["event"] == "runtime.started"
        )
        assert started["data"]["requested_cyber_access_program"] == "daybreak_blue"
        assert tokens.access_token not in private
    finally:
        bridge.close()
        store.close()


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


def test_sdk_owned_scope_context_is_forwarded_without_opening_authentication_inputs():
    context = {
        "CODEX_SECURITY_SCAN_ID": "a" * 32,
        "CODEX_SECURITY_TARGET_ID": "target_sha256_" + "b" * 64,
        "CODEX_SECURITY_TARGET_KIND": "git_revision",
        "CODEX_SECURITY_TARGET_REVISION": "c" * 40,
        "CODEX_SECURITY_STARTED_AT": "2026-10-20T00:00:00Z",
        "CODEX_SECURITY_TARGET_PATHS_FILE": "/artifacts/scan/scope.json",
        "CODEX_SECURITY_PLUGIN_ROOT": "/opt/evander/codex-security/plugin",
        "CODEX_SECURITY_REPOSITORY": "/artifacts/scan/checkout",
        "CODEX_SECURITY_SCAN_DIR": "/artifacts/scan/reports/review",
    }
    _, _, env = validate_request(
        {"argv": ["exec", "review"], "cwd": "/artifacts/scan", "environment": context}
    )
    assert env == context
    for field, value in (
        ("CODEX_SECURITY_SCAN_ID", "../other"),
        ("CODEX_SECURITY_TARGET_PATHS_FILE", "/etc/passwd"),
        ("CODEX_SECURITY_TARGET_KIND", "remote_url"),
        ("OPENAI_API_KEY", "secret"),
    ):
        with pytest.raises(ValueError):
            validate_request(
                {
                    "argv": ["exec", "review"],
                    "cwd": "/artifacts/scan",
                    "environment": {**context, field: value},
                }
            )


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


@pytest.fixture
def draining_broker(tmp_path):
    from evander.worker.private_diagnostics import PrivateJobDiagnostics

    store = PrivateJobDiagnostics(tmp_path / "private")
    capture = store.begin("scan-1")
    started, closing, release, drained, fail_close = (threading.Event() for _ in range(5))

    class SlowCloseProcess(NativeProcess):
        def __init__(self, *args, stderr_handler, **kwargs):
            super().__init__(*args, **kwargs)
            self.stderr = stderr_handler
            self.returncode = None

        def request(self, method, params):
            if method == "turn/start":
                started.set()
                return {}
            return super().request(method, params)

        def close(self):
            closing.set()
            assert release.wait(5), "Test did not release native shutdown"
            self.stderr("terminal private stderr")
            self.returncode = -15
            self.running = False
            if fail_close.is_set():
                raise RuntimeError("Synthetic native shutdown failure")

    tokens = CodexExternalAuthTokens("synthetic-token", "account-1", "test")
    bridge = CodexRuntimeBridge(
        path=tmp_path / "broker.sock",
        command_factory=lambda *args: ["native"],
        tokens=tokens,
        refresh_tokens=lambda **kwargs: tokens,
        http_client=unused_http_client(),
        process_factory=SlowCloseProcess,
        diagnostics=capture,
        on_drained=drained.set,
    )
    bridge.start()
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(3)
    client.connect(str(bridge.path))
    client.sendall(json.dumps({"argv": ["exec", "scan"], "cwd": "/home/agent"}).encode() + b"\n")
    assert started.wait(3)
    try:
        yield SimpleNamespace(
            bridge=bridge,
            capture=capture,
            closing=closing,
            release=release,
            drained=drained,
            fail_close=fail_close,
        )
    finally:
        release.set()
        bridge.close()
        client.close()
        store.close()


def test_shutdown_drains_terminal_diagnostics_before_returning(draining_broker, monkeypatch):
    state = draining_broker
    relay_closed, returned = threading.Event(), threading.Event()
    original_close = state.bridge.http_relay.close

    def close_relay():
        original_close()
        relay_closed.set()

    monkeypatch.setattr(state.bridge.http_relay, "close", close_relay)
    results = []

    def close_bridge():
        results.append(state.bridge.close())
        returned.set()

    closer = threading.Thread(target=close_bridge)
    closer.start()
    try:
        assert state.closing.wait(3) and relay_closed.wait(3)
        assert not returned.wait(0.1), "Broker returned before the native writer drained"
    finally:
        state.release.set()
        closer.join(3)
    assert not closer.is_alive() and results == [True]
    state.capture.finish(status="stopped")
    events = [
        json.loads(line)
        for line in (state.capture.directory / "events.jsonl").read_text().splitlines()
    ]
    assert any(
        event["event"] == "native.stderr" and event["data"]["line"] == "terminal private stderr"
        for event in events
    )
    closed = next(event for event in events if event["event"] == "runtime.closed")
    assert closed["data"]["native_exit_code"] == -15
    assert events[-1]["event"] == "worker.completed"


def test_shutdown_timeout_keeps_late_native_records_available(draining_broker):
    state = draining_broker
    assert state.bridge.close(timeout_seconds=0.01) is False
    assert state.closing.wait(3)
    assert not state.bridge.wait_closed() and not state.drained.is_set()
    state.release.set()
    assert state.drained.wait(3) and state.bridge.wait_closed()
    state.capture.finish(status="stopped")
    text = (state.capture.directory / "events.jsonl").read_text()
    assert "runtime.shutdown_timeout" in text
    assert "terminal private stderr" in text and '"native_exit_code": -15' in text


def test_native_close_error_does_not_leave_a_capture_waiting_forever(draining_broker):
    state = draining_broker
    state.fail_close.set()
    state.release.set()
    assert state.bridge.close() and state.bridge.wait_closed()
    state.capture.finish(status="stopped")
    text = (state.capture.directory / "events.jsonl").read_text()
    assert "runtime.close_failed" in text and "runtime.closed" in text
    assert "terminal private stderr" in text


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
        http_client=unused_http_client(),
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
        assert login["chatgptAccountId"] == "account-1"
        assert login["accessToken"] != "private-test-token"
        assert "private-test-token" not in json.dumps(native[0].calls)
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
        http_client=unused_http_client(),
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
def test_native_refresh_returns_only_placeholder_and_keeps_account_pin(tmp_path, same_account):
    refreshes = []
    initial = CodexExternalAuthTokens("initial-private-token", "account-1", None)
    replies = []

    class RefreshingNative(NativeProcess):
        def request(self, method, params):
            if method == "thread/start":
                replies.append(
                    self.handlers["account/chatgptAuthTokens/refresh"](
                        {"previousAccountId": "account-1" if same_account else "other-account"}
                    )
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
        http_client=unused_http_client(),
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
        assert refreshes == []
        if same_account:
            assert replies[0]["chatgptAccountId"] == "account-1"
            assert replies[0]["accessToken"] == bridge.native_tokens.access_token
        assert "private-token" not in json.dumps(replies)
        assert "private-token" not in json.dumps(frames)
    finally:
        bridge.close()


def test_runtime_bounds_clients_before_they_supply_a_command(tmp_path):
    bridge = CodexRuntimeBridge(
        path=tmp_path / "broker.sock",
        command_factory=lambda env, args: ["native"],
        tokens=CodexExternalAuthTokens("test-token", "account-1", None),
        refresh_tokens=lambda **kwargs: None,
        http_client=unused_http_client(),
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


def test_broker_records_parent_and_child_usage_outside_private_diagnostics(broker, tmp_path):
    from evander.worker.model_usage import SubscriptionUsageRecorder, WorkerUsageJournal

    bridge, _ = broker
    journal = WorkerUsageJournal(tmp_path / "usage.db")
    bridge.usage_recorder_factory = lambda model: SubscriptionUsageRecorder(
        job_id="job",
        session_id="session",
        context={"model": model},
        sink=journal.append,
    )

    class UsageProcess(NativeProcess):
        def request(self, method, params):
            if method == "turn/start":
                for thread, turn, total in [
                    ("thread-test", "parent-turn", 50),
                    ("child-thread", "child-turn", 10),
                ]:
                    self.notify_handler("turn/started", {"threadId": thread, "turn": {"id": turn}})
                    self.notify_handler(
                        "thread/tokenUsage/updated",
                        {
                            "threadId": thread,
                            "turnId": turn,
                            "tokenUsage": {
                                "total": {"totalTokens": total},
                                "last": {"totalTokens": total},
                            },
                        },
                    )
                    self.notify_handler(
                        "turn/completed",
                        {"threadId": thread, "turn": {"id": turn, "status": "completed"}},
                    )
            return super().request(method, params)

    bridge.process_factory = UsageProcess
    frames = exchange(bridge, {"argv": ["exec", "test"], "cwd": "/home/agent", "environment": {}})
    assert frames[-1] == {"exit_code": 0}
    assert bridge.close()
    assert [event.total_tokens for event in journal.page("job").events] == [50, 10]


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
        http_client=unused_http_client(),
        process_factory=factory,
    )
    bridge.start()
    try:
        yield bridge, processes
    finally:
        bridge.close()


@pytest.mark.parametrize(
    "program", [None, "standard", "daybreak_blue", "daybreak_red", "unknown_program"]
)
def test_operator_runtime_profile_validates_cyber_access_program(tmp_path, program):
    path = tmp_path / "profiles.json"
    profile = {"command_prefix": ["scanner"]}
    if program is not None:
        profile["default_cyber_access_program"] = program
    path.write_text(json.dumps({"scan": profile}))
    path.chmod(0o600)
    if program == "unknown_program":
        with pytest.raises(ValueError, match="default_cyber_access_program"):
            load_runtime_profiles(str(path))
    else:
        assert load_runtime_profiles(str(path))["scan"].default_cyber_access_program == program


@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize(
    "default,explicit,expected",
    [
        (None, None, None),
        (None, "daybreak_blue", "daybreakBlue"),
        ("daybreak_blue", None, "daybreakBlue"),
        ("daybreak_red", None, "daybreakRed"),
        ("daybreak_blue", "standard", "standard"),
    ],
)
def test_runtime_applies_operator_program_default_to_each_exec_turn(
    broker, resumed, default, explicit, expected
):
    bridge, processes = broker
    bridge.default_cyber_access_program = default
    argv = ["exec"]
    if explicit:
        argv += ["--cyber-access-program", explicit]
    if resumed:
        argv += ["resume", "existing-thread"]
    frames = exchange(bridge, {"argv": [*argv, "scan"], "cwd": "/home/agent"})
    assert frames[-1] == {"exit_code": 0}
    turn = next(params for method, params in processes[0].calls if method == "turn/start")
    if expected is None:
        assert "cyberAccessProgram" not in turn
    else:
        assert turn["cyberAccessProgram"] == expected


def test_runtime_rejects_unrecognized_cyber_program_before_starting_native(broker):
    bridge, processes = broker
    frames = exchange(
        bridge,
        {
            "argv": ["exec", "--cyber-access-program", "unknown_program", "scan"],
            "cwd": "/home/agent",
        },
    )
    assert frames[-1] == {"exit_code": 1}
    assert processes == []


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


@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("child_status", ["completed", "failed"])
def test_exec_waits_for_parent_and_omits_child_messages_and_usage(broker, resumed, child_status):
    """An independent child audit must not terminate or overwrite its parent scan."""
    bridge, processes = broker
    finish_parent = threading.Event()
    child_finished = threading.Event()
    emitters = []

    class ChildFirstNative(NativeProcess):
        def request(self, method, params):
            if method != "turn/start":
                return super().request(method, params)
            self.calls.append((method, params))
            parent_id = params["threadId"]
            self.notify_handler(
                "item/completed",
                {
                    "threadId": "child-audit",
                    "item": {
                        "id": "child-result",
                        "type": "agentMessage",
                        "text": "child findings",
                    },
                },
            )
            self.notify_handler(
                "thread/tokenUsage/updated",
                {
                    "threadId": "child-audit",
                    "tokenUsage": {"last": {"inputTokens": 9000, "outputTokens": 9000}},
                },
            )
            self.notify_handler(
                "turn/completed", {"threadId": "child-audit", "turn": {"status": child_status}}
            )
            child_finished.set()
            self.notify_handler(
                "item/completed",
                {
                    "threadId": parent_id,
                    "item": {
                        "id": "parent-progress",
                        "type": "agentMessage",
                        "text": "still scanning",
                    },
                },
            )

            def finish():
                if not finish_parent.wait(5):
                    return
                self.notify_handler(
                    "thread/tokenUsage/updated",
                    {
                        "threadId": parent_id,
                        "tokenUsage": {
                            "last": {"inputTokens": 30, "cachedInputTokens": 4, "outputTokens": 12}
                        },
                    },
                )
                self.notify_handler(
                    "item/completed",
                    {
                        "threadId": parent_id,
                        "item": {
                            "id": "parent-report",
                            "type": "agentMessage",
                            "text": "report sealed",
                        },
                    },
                )
                self.notify_handler(
                    "turn/completed", {"threadId": parent_id, "turn": {"status": "completed"}}
                )

            emitter = threading.Thread(target=finish, daemon=True)
            emitter.start()
            emitters.append(emitter)
            return {}

    def factory(*args, **kwargs):
        process = ChildFirstNative(*args, **kwargs)
        processes.append(process)
        return process

    bridge.process_factory = factory
    argv = ["exec", "--json", *(["resume", "thread-test"] if resumed else []), "scan"]
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3)
            client.connect(str(bridge.path))
            client.sendall((json.dumps({"argv": argv, "cwd": "/home/agent"}) + "\n").encode())
            with client.makefile("rb", buffering=0) as stream:
                started = json.loads(json.loads(stream.readline())["text"])
                assert started == {"type": "thread.started", "thread_id": "thread-test"}
                assert child_finished.wait(3)
                progress = json.loads(json.loads(stream.readline())["text"])
                assert progress["item"]["text"] == "still scanning"
                # No completion or exit may be sent while the parent is blocked.
                client.settimeout(0.1)
                with pytest.raises(TimeoutError):
                    client.recv(1, socket.MSG_PEEK)
                client.settimeout(3)
                finish_parent.set()
                report = json.loads(json.loads(stream.readline())["text"])
                assert report["item"]["text"] == "report sealed"
                completed = json.loads(json.loads(stream.readline())["text"])
                assert completed == {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 30, "cached_input_tokens": 4, "output_tokens": 12},
                }
                assert json.loads(stream.readline()) == {"exit_code": 0}
        opened = next(method for method, _ in processes[0].calls if method.startswith("thread/"))
        assert opened == ("thread/resume" if resumed else "thread/start")
    finally:
        finish_parent.set()
        for emitter in emitters:
            emitter.join(timeout=3)


def test_runtime_sockets_work_in_long_private_session_paths(tmp_path):
    """Both sockets must bind where the container's private mount expects them."""
    root = tmp_path / ("session-" + "a" * 80) / "codex-runtimes" / ("scan-" + "b" * 32)
    root.mkdir(parents=True)
    bridge = CodexRuntimeBridge(
        path=root / "broker.sock",
        command_factory=lambda env, args: ["native"],
        tokens=CodexExternalAuthTokens("worker-only-token", "account-1", None),
        refresh_tokens=lambda **kwargs: pytest.fail("Unexpected refresh"),
        http_client=unused_http_client(),
        process_factory=NativeProcess,
    )
    assert len(os.fsencode(bridge.path)) > 107
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    alias = Path(f"/proc/self/fd/{parent}")
    try:
        bridge.start()
        frames = exchange(
            SimpleNamespace(path=alias / "broker.sock"),
            {"argv": ["exec", "--json", "scan"], "cwd": "/home/agent"},
        )
        assert frames[-1] == {"exit_code": 0}
        with UnixConnection(str(alias / "models.sock"), timeout=3) as connection:
            connection.request("GET", "/forbidden-endpoint")
            reply = connection.getresponse()
            assert reply.status == 403
            assert b"worker-only-token" not in reply.read()
        assert bridge.path.is_socket() and bridge.http_relay.path.is_socket()
        assert bridge.path.stat().st_mode & 0o777 == 0o666
        assert bridge.http_relay.path.stat().st_mode & 0o777 == 0o666
    finally:
        bridge.close()
        os.close(parent)
    assert not bridge.path.exists() and not bridge.http_relay.path.exists()


@pytest.mark.parametrize(
    "detail,expected",
    [
        ("private token quota exceeded", "quota"),
        ("private token authentication failed", "authentication"),
        ("private token cyberPolicy403 denial", "policy"),
    ],
)
def test_runtime_failure_categories_keep_private_details_out_of_public_errors(detail, expected):
    from evander.worker.codex_runtime_bridge import public_runtime_failure

    public = public_runtime_failure({"message": detail})
    assert expected in public.lower()
    assert "private token" not in public


def test_sdk_login_status_uses_managed_profile_without_starting_a_model(tmp_path):
    tokens = CodexExternalAuthTokens("private-subscription-token", "account-1", "test")
    bridge = CodexRuntimeBridge(
        path=tmp_path / "broker.sock",
        tokens=tokens,
        refresh_tokens=lambda **_: tokens,
        http_client=unused_http_client(),
        command_factory=lambda *_: pytest.fail("No native model process expected"),
        process_factory=lambda *_args, **_kwargs: pytest.fail("No native model process expected"),
    )
    bridge.start()
    try:
        frames = exchange(bridge, {"argv": ["login", "status"], "cwd": "/home/agent"})
        assert frames[-1] == {"exit_code": 0}
        assert "ChatGPT" in json.dumps(frames)
        assert tokens.access_token not in json.dumps(frames)
    finally:
        bridge.close()
