from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

from evander.codex_auth import CodexAuthMode, CodexTurnSpec
from evander.worker.codex_auth_broker import CodexExternalAuthTokens
from evander.worker.codex_egress_proxy import RestrictedCodexEgressProxy
from evander.worker.codex_subscription import CodexSubscriptionTurnRunner


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
    assert resume["config"]["mcp_servers"]["evander"]["url"].endswith("/mcp")
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
