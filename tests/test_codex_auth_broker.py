from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from evander.worker.codex_auth_broker import (
    CodexAuthBroker,
    CodexAuthBrokerError,
)


class FakeManagedClient:
    def __init__(self, auth_path: Path, **kwargs: Any) -> None:
        self.auth_path = auth_path
        self.notification_handler = kwargs.get("notification_handler")
        self.running = False
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.refresh_count = 0

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
        request_params = dict(params or {})
        self.requests.append((method, request_params))
        if method == "account/read":
            if request_params.get("refreshToken"):
                self.refresh_count += 1
                _write_auth(
                    self.auth_path,
                    access_token=_jwt(
                        plan="plus", expiration=int(time.time()) + 3600, marker="new"
                    ),
                )
            return {
                "account": {"type": "chatgpt", "email": "owner@example.test", "planType": "plus"},
                "requiresOpenaiAuth": True,
            }
        if method == "account/login/start":
            return {
                "type": "chatgptDeviceCode",
                "loginId": "login-1",
                "verificationUrl": "https://example.test/device",
                "userCode": "ABCD-EFGH",
            }
        if method in {"account/login/cancel", "account/logout"}:
            return {}
        raise AssertionError(f"unexpected request: {method}")

    def close(self) -> None:
        self.running = False

    def complete_login(self, *, success: bool = True) -> None:
        assert self.notification_handler is not None
        self.notification_handler(
            "account/login/completed",
            {"loginId": "login-1", "success": success, "error": None},
        )


class NotificationDuringCancelClient(FakeManagedClient):
    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if method != "account/login/cancel":
            return super().request(method, params, timeout_seconds=timeout_seconds)
        notification_returned = threading.Event()

        def notify_from_reader() -> None:
            assert self.notification_handler is not None
            self.notification_handler(
                "account/login/completed",
                {"loginId": "login-1", "success": True, "error": None},
            )
            notification_returned.set()

        reader = threading.Thread(target=notify_from_reader)
        reader.start()
        if not notification_returned.wait(timeout=1):
            raise AssertionError("app-server reader was blocked by the broker request lock")
        reader.join(timeout=1)
        return {}


def test_broker_has_one_durable_auth_file_and_one_owner(tmp_path: Path) -> None:
    broker = CodexAuthBroker(auth_home=tmp_path / "codex-auth", client_factory=lambda *a, **k: None)
    try:
        assert broker.auth_path == tmp_path / "codex-auth" / "auth.json"
        assert (tmp_path / "codex-auth").stat().st_mode & 0o777 == 0o700
        with pytest.raises(CodexAuthBrokerError, match="already owns"):
            CodexAuthBroker(auth_home=tmp_path / "codex-auth", client_factory=lambda *a, **k: None)
    finally:
        broker.close()


def test_concurrent_stale_token_refresh_happens_once(tmp_path: Path) -> None:
    auth_home = tmp_path / "codex-auth"
    auth_home.mkdir()
    old_token = _jwt(plan="plus", expiration=int(time.time()) + 3600, marker="old")
    _write_auth(auth_home / "auth.json", access_token=old_token)
    clients: list[FakeManagedClient] = []

    def factory(*args: Any, **kwargs: Any) -> FakeManagedClient:
        del args
        client = FakeManagedClient(auth_home / "auth.json", **kwargs)
        clients.append(client)
        return client

    broker = CodexAuthBroker(auth_home=auth_home, client_factory=factory)
    results = []

    def read_tokens() -> None:
        results.append(broker.external_tokens(previous_access_token=old_token))

    threads = [threading.Thread(target=read_tokens) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    try:
        assert len(results) == 8
        assert len({result.access_token for result in results}) == 1
        assert results[0].access_token != old_token
        assert clients[0].refresh_count == 1
    finally:
        broker.close()


def test_device_login_exposes_only_operator_instructions_and_status(tmp_path: Path) -> None:
    auth_home = tmp_path / "codex-auth"
    clients: list[FakeManagedClient] = []

    def factory(*args: Any, **kwargs: Any) -> FakeManagedClient:
        del args
        client = FakeManagedClient(auth_home / "auth.json", **kwargs)
        clients.append(client)
        return client

    broker = CodexAuthBroker(auth_home=auth_home, client_factory=factory)
    try:
        started = broker.start_device_login()
        assert started == {
            "login_id": "login-1",
            "status": "pending",
            "verification_url": "https://example.test/device",
            "user_code": "ABCD-EFGH",
            "error": None,
        }
        clients[0].complete_login()
        assert broker.device_login_status("login-1")["status"] == "completed"
    finally:
        broker.close()


def test_login_notification_never_blocks_reader_behind_cancel_request(tmp_path: Path) -> None:
    auth_home = tmp_path / "codex-auth"

    def factory(*args: Any, **kwargs: Any) -> NotificationDuringCancelClient:
        del args
        return NotificationDuringCancelClient(auth_home / "auth.json", **kwargs)

    broker = CodexAuthBroker(auth_home=auth_home, client_factory=factory)
    try:
        broker.start_device_login()

        cancelled = broker.cancel_device_login("login-1")

        assert cancelled["status"] == "completed"
        assert broker.device_login_status("login-1")["status"] == "completed"
    finally:
        broker.close()


def _write_auth(path: Path, *, access_token: str) -> None:
    path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "last_refresh": "2026-07-21T12:00:00Z",
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": "refresh-secret",
                    "account_id": "account-1",
                    "id_token": "id-secret",
                },
            }
        ),
        encoding="utf-8",
    )


def _jwt(*, plan: str, expiration: int, marker: str) -> str:
    header = _b64({"alg": "none", "typ": "JWT"})
    payload = _b64(
        {
            "exp": expiration,
            "marker": marker,
            "https://api.openai.com/auth": {"chatgpt_plan_type": plan},
        }
    )
    return f"{header}.{payload}.signature"


def _b64(value: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")
