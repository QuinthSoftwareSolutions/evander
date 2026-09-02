from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from evander.codex_auth import CodexAuthMode, CodexTurnSpec
from evander.worker.codex_app_server import CodexAppServerError, CodexAppServerProcess
from evander.worker.codex_auth_broker import CodexExternalAuthTokens
from evander.worker.codex_egress_proxy import RestrictedCodexEgressProxy, _tunnel
from evander.worker.codex_subscription import (
    CodexSubscriptionTurnRunner,
    write_completion_marker_no_follow,
)


class FakeBroker:
    def __init__(self) -> None:
        self.refreshes = 0

    def external_tokens(self, **kwargs: Any) -> CodexExternalAuthTokens:
        assert kwargs["previous_access_token"] == "access-secret"
        self.refreshes += 1
        return CodexExternalAuthTokens("rotated-secret", "account-1", "plus")


class FakeTurnProcess:
    instances: list["FakeTurnProcess"] = []

    def __init__(self, command: tuple[str, ...], **kwargs: Any) -> None:
        self.command = command
        self.notification_handler = kwargs["notification_handler"]
        self.refresh_handler = kwargs["server_request_handlers"][
            "account/chatgptAuthTokens/refresh"
        ]
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.running = False
        self.closed = False
        self.refresh_response: dict[str, Any] | None = None
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.running = True

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        del timeout_seconds
        value = dict(params or {})
        self.requests.append((method, value))
        if method == "account/login/start":
            return {"type": "chatgptAuthTokens"}
        if method == "thread/list":
            return {"data": [{"id": "existing-thread"}]}
        if method == "thread/resume":
            return {"thread": {"id": value["threadId"]}}
        if method == "thread/start":
            return {"thread": {"id": "new-thread"}}
        if method == "turn/start":
            self.refresh_response = self.refresh_handler(
                {"reason": "unauthorized", "previousAccountId": "account-1"}
            )
            self.notification_handler(
                "item/completed",
                {
                    "threadId": value["threadId"],
                    "turnId": "turn-1",
                    "item": {"type": "agentMessage", "id": "item-1", "text": "Done."},
                },
            )
            self.notification_handler(
                "turn/completed",
                {
                    "threadId": value["threadId"],
                    "turn": {"id": "turn-1", "status": "completed", "items": []},
                },
            )
            return {"turn": {"id": "turn-1", "status": "inProgress", "items": []}}
        raise AssertionError(f"unexpected method: {method}")

    def close(self) -> None:
        self.closed = True
        self.running = False


class ExitingTurnProcess(FakeTurnProcess):
    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if method != "turn/start":
            return super().request(method, params, timeout_seconds=timeout_seconds)
        self.requests.append((method, dict(params or {})))
        self.running = False
        return {"turn": {"id": "turn-1", "status": "inProgress", "items": []}}


class RateLimitedTurnProcess(FakeTurnProcess):
    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if method != "turn/start":
            return super().request(method, params, timeout_seconds=timeout_seconds)
        value = dict(params or {})
        self.requests.append((method, value))
        self.notification_handler(
            "turn/completed",
            {
                "threadId": value["threadId"],
                "turn": {
                    "id": "turn-rate-limited",
                    "status": "failed",
                    "error": {
                        "message": "usage rate limit reached",
                        "resetsAt": "2026-08-01T12:30:00Z",
                    },
                },
            },
        )
        return {"turn": {"id": "turn-rate-limited", "status": "inProgress", "items": []}}


class RateLimitedRequestProcess(FakeTurnProcess):
    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if method != "turn/start":
            return super().request(method, params, timeout_seconds=timeout_seconds)
        self.requests.append((method, dict(params or {})))
        raise CodexAppServerError("Codex app-server request failed: usage rate limit reached")


def test_subscription_runner_resumes_thread_and_keeps_tokens_out_of_logs(tmp_path: Path) -> None:
    FakeTurnProcess.instances.clear()
    broker = FakeBroker()
    metadata: list[dict[str, Any]] = []
    runner = CodexSubscriptionTurnRunner(
        command=("docker", "exec", "container-1", "codex", "app-server", "--stdio"),
        turn=CodexTurnSpec(
            auth_mode=CodexAuthMode.CHATGPT_SUBSCRIPTION,
            prompt="Continue the task",
            model="gpt-5.4",
            resume_mode="same_context",
            mcp_gateway_url="http://gateway.internal:8000/mcp",
        ),
        cwd="/home/agent/repos/project",
        broker=broker,  # type: ignore[arg-type]
        initial_tokens=CodexExternalAuthTokens("access-secret", "account-1", "plus"),
        log_path=tmp_path / "turn.log",
        finished_marker=tmp_path / "turn.exit",
        metadata_callback=metadata.append,
        process_factory=FakeTurnProcess,  # type: ignore[arg-type]
    )

    runner.run()

    process = FakeTurnProcess.instances[0]
    assert (tmp_path / "turn.exit").read_text(encoding="utf-8") == "0"
    assert (tmp_path / "turn.log").read_text(encoding="utf-8") == (
        "session id: existing-thread\nDone.\n"
    )
    assert "access-secret" not in (tmp_path / "turn.log").read_text(encoding="utf-8")
    assert process.refresh_response == {
        "accessToken": "rotated-secret",
        "chatgptAccountId": "account-1",
        "chatgptPlanType": "plus",
    }
    assert broker.refreshes == 1
    resume = next(params for method, params in process.requests if method == "thread/resume")
    assert resume["threadId"] == "existing-thread"
    assert resume["modelProvider"] == "openai"
    assert resume["sandbox"] == "danger-full-access"
    assert resume["config"]["mcp_servers"]["evander"]["url"].endswith("/mcp")
    assert "dynamicTools" not in resume
    turn_start = next(params for method, params in process.requests if method == "turn/start")
    assert "dynamicTools" not in turn_start
    assert metadata[-1]["codex_subscription_exit_code"] == 0
    assert all("access" not in str(entry).casefold() for entry in metadata)


def test_restricted_proxy_rejects_unlisted_connect_destination() -> None:
    proxy = RestrictedCodexEgressProxy(host="127.0.0.1", port=0, allowed_hosts=["api.openai.com"])
    proxy.start()
    try:
        with socket.create_connection(("127.0.0.1", proxy.bound_port), timeout=2) as client:
            client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n")
            response = client.recv(4096)
        assert response.startswith(b"HTTP/1.1 403")
    finally:
        proxy.close()


def test_completion_marker_does_not_follow_agent_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "auth.json"
    victim.write_text("keep-me", encoding="utf-8")
    marker = tmp_path / "turn.exit"
    marker.symlink_to(victim)

    write_completion_marker_no_follow(marker, 0)

    assert victim.read_text(encoding="utf-8") == "keep-me"
    assert not marker.is_symlink()
    assert marker.read_text(encoding="utf-8") == "0"


def test_completion_marker_replaces_nonempty_agent_directory(tmp_path: Path) -> None:
    marker = tmp_path / "turn.exit"
    marker.mkdir()
    (marker / "blocker").write_text("agent-created", encoding="utf-8")

    write_completion_marker_no_follow(marker, 1)

    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == "1"
    assert list(tmp_path.glob(".turn.exit.stale-*")) == []


@pytest.mark.parametrize(
    "server_script",
    [
        (
            "import json, sys, time\n"
            "request = json.loads(sys.stdin.readline())\n"
            "print(json.dumps({'id': request['id'], 'error': {'message': 'rejected'}}), "
            "flush=True)\n"
            "time.sleep(30)\n"
        ),
        "import sys, time\nsys.stdin.readline()\ntime.sleep(30)\n",
    ],
    ids=["initialize-error", "initialize-timeout"],
)
def test_app_server_start_tears_down_process_when_initialization_fails(
    server_script: str,
) -> None:
    process = CodexAppServerProcess(
        (sys.executable, "-u", "-c", server_script),
        request_timeout_seconds=0.1,
    )
    try:
        with pytest.raises(CodexAppServerError, match="initialize"):
            process.start()

        child = process._process  # noqa: SLF001 - assert the owned child was actually reaped.
        assert child is not None
        assert child.poll() is not None
        assert process.running is False
    finally:
        process.close()


def test_subscription_runner_fails_when_app_server_exits_before_completion(
    tmp_path: Path,
) -> None:
    runner = CodexSubscriptionTurnRunner(
        command=("codex", "app-server", "--stdio"),
        turn=CodexTurnSpec(
            auth_mode=CodexAuthMode.CHATGPT_SUBSCRIPTION,
            prompt="Continue the task",
            model="gpt-5.4",
        ),
        cwd="/home/agent",
        broker=FakeBroker(),  # type: ignore[arg-type]
        initial_tokens=CodexExternalAuthTokens("access-secret", "account-1", "plus"),
        log_path=tmp_path / "turn.log",
        finished_marker=tmp_path / "turn.exit",
        process_factory=ExitingTurnProcess,  # type: ignore[arg-type]
    )

    runner.run()

    assert (tmp_path / "turn.exit").read_text(encoding="utf-8") == "1"
    assert "exited before reporting turn completion" in (tmp_path / "turn.log").read_text(
        encoding="utf-8"
    )


def test_subscription_runner_classifies_mid_turn_rate_limit_for_durable_replay(
    tmp_path: Path,
) -> None:
    metadata: list[dict[str, Any]] = []
    runner = CodexSubscriptionTurnRunner(
        command=("codex", "app-server", "--stdio"),
        turn=CodexTurnSpec(
            auth_mode=CodexAuthMode.CHATGPT_SUBSCRIPTION,
            subscription_profile_id="team-a",
            prompt="Continue the task",
            model="gpt-5.4",
        ),
        cwd="/home/agent",
        broker=FakeBroker(),  # type: ignore[arg-type]
        initial_tokens=CodexExternalAuthTokens("access-secret", "account-1", "plus"),
        log_path=tmp_path / "turn.log",
        finished_marker=tmp_path / "turn.exit",
        metadata_callback=metadata.append,
        process_factory=RateLimitedTurnProcess,  # type: ignore[arg-type]
    )

    runner.run()

    assert (tmp_path / "turn.exit").read_text(encoding="utf-8") == "1"
    assert metadata[-1]["subscription_profile_id"] == "team-a"
    assert metadata[-1]["codex_subscription_deferral"] == {
        "profile_id": "team-a",
        "reason": "rate_limited",
        "retry_at": "2026-08-01T12:30:00Z",
    }


def test_subscription_runner_classifies_rate_limit_request_error_for_durable_replay(
    tmp_path: Path,
) -> None:
    metadata: list[dict[str, Any]] = []
    runner = CodexSubscriptionTurnRunner(
        command=("codex", "app-server", "--stdio"),
        turn=CodexTurnSpec(
            auth_mode=CodexAuthMode.CHATGPT_SUBSCRIPTION,
            subscription_profile_id="team-a",
            prompt="Continue the task",
            model="gpt-5.4",
        ),
        cwd="/home/agent",
        broker=FakeBroker(),  # type: ignore[arg-type]
        initial_tokens=CodexExternalAuthTokens("access-secret", "account-1", "plus"),
        log_path=tmp_path / "turn.log",
        finished_marker=tmp_path / "turn.exit",
        metadata_callback=metadata.append,
        process_factory=RateLimitedRequestProcess,  # type: ignore[arg-type]
    )

    runner.run()

    assert (tmp_path / "turn.exit").read_text(encoding="utf-8") == "1"
    assert metadata[-1]["codex_subscription_deferral"] == {
        "profile_id": "team-a",
        "reason": "rate_limited",
    }


def test_subscription_runner_does_not_start_process_after_early_cancellation(
    tmp_path: Path,
) -> None:
    factory_entered = threading.Event()
    release_factory = threading.Event()
    processes: list[FakeTurnProcess] = []

    def process_factory(*args: Any, **kwargs: Any) -> FakeTurnProcess:
        factory_entered.set()
        assert release_factory.wait(timeout=2)
        process = FakeTurnProcess(*args, **kwargs)
        processes.append(process)
        return process

    runner = CodexSubscriptionTurnRunner(
        command=("codex", "app-server", "--stdio"),
        turn=CodexTurnSpec(
            auth_mode=CodexAuthMode.CHATGPT_SUBSCRIPTION,
            prompt="Continue the task",
            model="gpt-5.4",
        ),
        cwd="/home/agent",
        broker=FakeBroker(),  # type: ignore[arg-type]
        initial_tokens=CodexExternalAuthTokens("access-secret", "account-1", "plus"),
        log_path=tmp_path / "turn.log",
        finished_marker=tmp_path / "turn.exit",
        process_factory=process_factory,  # type: ignore[arg-type]
    )

    runner.start()
    assert factory_entered.wait(timeout=2)
    runner.cancel()
    release_factory.set()

    assert runner.join(timeout_seconds=2)
    assert len(processes) == 1
    assert processes[0].requests == []
    assert not processes[0].running
    assert (tmp_path / "turn.exit").read_text(encoding="utf-8") == "1"


def test_restricted_proxy_expires_idle_tunnel_without_helper_threads() -> None:
    client, client_peer = socket.socketpair()
    upstream, upstream_peer = socket.socketpair()
    thread = threading.Thread(
        target=_tunnel,
        args=(client, upstream),
        kwargs={"idle_timeout_seconds": 0.1},
    )
    try:
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive()
    finally:
        for connection in (client, client_peer, upstream, upstream_peer):
            connection.close()


def test_restricted_proxy_rejects_connections_over_the_host_thread_limit() -> None:
    proxy = RestrictedCodexEgressProxy(
        host="127.0.0.1",
        port=0,
        allowed_hosts=["api.openai.com"],
        max_connections=1,
    )
    proxy.start()
    server = proxy._server  # noqa: SLF001
    assert server is not None
    assert server._connection_slots.acquire(blocking=False)  # noqa: SLF001
    request, peer = socket.socketpair()
    try:
        server.process_request(request, ("local", 0))
        assert peer.recv(4096).startswith(b"HTTP/1.1 503")
    finally:
        server._connection_slots.release()  # noqa: SLF001
        peer.close()
        proxy.close()


def test_restricted_proxy_rejects_non_positive_connection_limit() -> None:
    with pytest.raises(ValueError, match="max_connections must be positive"):
        RestrictedCodexEgressProxy(host="127.0.0.1", port=0, max_connections=0)
