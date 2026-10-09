from __future__ import annotations

import httpx
import pytest

from evander.email.http_utils import ProviderRequestError, request_with_retries


@pytest.mark.parametrize("generic_request", [True, False])
def test_request_adapters_preserve_method_and_optional_arguments(generic_request: bool) -> None:
    calls = []

    def respond(*args, **kwargs):
        calls.append((args, kwargs))
        return httpx.Response(200, request=httpx.Request("GET", "https://mail.test/messages"))

    class MethodClient:
        get = staticmethod(respond)

    class GenericClient:
        request = staticmethod(respond)

    client = GenericClient() if generic_request else MethodClient()
    response = request_with_retries(
        client=client,
        provider="mail",
        operation="list_messages",
        method="get",
        url="https://mail.test/messages",
        headers={},
        params={"limit": 10},
    )

    assert response.status_code == 200
    expected_args = (
        ("GET", "https://mail.test/messages")
        if generic_request
        else ("https://mail.test/messages",)
    )
    assert calls == [(expected_args, {"headers": {}, "params": {"limit": 10}})]


def test_retry_preserves_request_payload_after_transient_failure(monkeypatch) -> None:
    calls = []
    sleeps = []
    monkeypatch.setattr(
        "evander.email.http_utils._sleep_with_jitter", lambda *args: sleeps.append(args)
    )

    class Client:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return httpx.Response(
                503 if len(calls) == 1 else 200,
                request=httpx.Request(method, url),
            )

    response = request_with_retries(
        client=Client(),
        provider="mail",
        operation="update_message",
        method="put",
        url="https://mail.test/messages/1",
        json={},
        max_attempts=2,
    )

    assert response.status_code == 200
    assert calls == [
        ("PUT", "https://mail.test/messages/1", {"json": {}}),
        ("PUT", "https://mail.test/messages/1", {"json": {}}),
    ]
    assert sleeps == [(1, 0.25, 2.0)]


def test_exhausted_network_retry_retains_prior_http_status_and_cause(monkeypatch) -> None:
    monkeypatch.setattr("evander.email.http_utils._sleep_with_jitter", lambda *args: None)
    attempts = []
    network_error = httpx.ConnectError("connection failed")

    def respond(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(503)
        raise network_error

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderRequestError) as raised:
            request_with_retries(
                client=client,
                provider="mail",
                operation="list_messages",
                method="GET",
                url="https://mail.test/messages",
                max_attempts=2,
            )

    assert len(attempts) == 2
    error = raised.value
    assert str(error) == (
        "mail.list_messages exhausted retries on transient network error: "
        "GET https://mail.test/messages status=503 attempts=2/2 "
        "error=ConnectError: connection failed"
    )
    assert (error.status_code, error.attempt, error.max_attempts) == (503, 2, 2)
    assert error.__cause__ is network_error


def test_non_idempotent_network_failure_stops_before_second_attempt(monkeypatch) -> None:
    sleeps = []
    attempts = []
    monkeypatch.setattr(
        "evander.email.http_utils._sleep_with_jitter", lambda *args: sleeps.append(args)
    )
    network_error = httpx.ReadTimeout("response timed out")

    def respond(request):
        attempts.append(request)
        raise network_error

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderRequestError) as raised:
            request_with_retries(
                client=client,
                provider="mail",
                operation="send_message",
                method="post",
                url="https://mail.test/messages",
                data={},
            )

    assert len(attempts) == 1
    assert sleeps == []
    error = raised.value
    assert str(error) == (
        "mail.send_message failed without retry on transient network error for "
        "non-idempotent method: POST https://mail.test/messages attempt=1/4 "
        "error=ReadTimeout: response timed out"
    )
    assert (error.status_code, error.attempt, error.max_attempts) == (None, 1, 4)
    assert error.__cause__ is network_error
