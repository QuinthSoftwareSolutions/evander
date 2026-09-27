from __future__ import annotations

import asyncio
import logging

import pytest
from starlette.routing import Route

from evander.admin.web import app as web_app


def _run(app, monkeypatch, clock):
    monkeypatch.setattr(web_app.time, "perf_counter", lambda: clock[0])

    async def send(message):
        pass

    middleware = web_app.AdminWebRequestTimingMiddleware(
        app,
        routes=[Route("/events/{opaque_id}", lambda request: None)],
        slow_request_threshold_ms=500,
    )
    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "GET",
                "path": "/events/secret-id",
                "root_path": "",
                "query_string": b"token=secret-token",
            },
            None,
            send,
        )
    )


def _messages(caplog):
    return [record.getMessage() for record in caplog.records if record.name == web_app.__name__]


def test_successful_sse_lifetime_is_not_request_latency(monkeypatch, caplog):
    clock = [0.0]

    async def app(scope, receive, send):
        clock[0] = 0.01
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream; charset=utf-8")],
            }
        )
        clock[0] = 0.02
        await send(
            {"type": "http.response.body", "body": b"event: snapshot\n\n", "more_body": True}
        )
        clock[0] = 300.0
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    _run(app, monkeypatch, clock)
    assert not _messages(caplog)


def test_slow_sse_start_is_logged_before_subscription_ends(monkeypatch, caplog):
    clock = [0.0]
    caplog.set_level(logging.WARNING, logger=web_app.__name__)

    async def app(scope, receive, send):
        clock[0] = 0.25
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send({"type": "http.response.body", "body": b"", "more_body": True})
        clock[0] = 2.0
        await send({"type": "http.response.body", "body": b"data: private\n\n", "more_body": True})
        assert len(_messages(caplog)) == 1
        clock[0] = 300.0

    _run(app, monkeypatch, clock)
    messages = _messages(caplog)
    assert len(messages) == 1
    assert "route=/events/{opaque_id}" in messages[0]
    assert "duration_ms=2000 slow=True response_start_ms=250 first_body_ms=2000" in messages[0]
    assert "phase=stream_start" in messages[0]
    assert "secret" not in messages[0]
    assert "private" not in messages[0]


@pytest.mark.parametrize("event_stream", [False, True])
def test_failure_after_headers_is_logged_and_reraised(monkeypatch, caplog, event_stream):
    clock = [0.0]

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream" if event_stream else b"application/json")
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"partial", "more_body": True})
        clock[0] = 0.125
        raise ValueError("private upstream error")

    with pytest.raises(ValueError, match="private upstream error"):
        _run(app, monkeypatch, clock)
    (message,) = _messages(caplog)
    assert "status=200" in message
    assert "failed=True" in message
    assert "total_ms=125" in message
    assert "private" not in message


def test_non_stream_response_records_total_duration(monkeypatch, caplog):
    clock = [0.0]

    async def app(scope, receive, send):
        clock[0] = 0.25
        await send({"type": "http.response.start", "status": 200, "headers": []})
        clock[0] = 1.0
        await send({"type": "http.response.body", "body": b"{}"})

    _run(app, monkeypatch, clock)
    (message,) = _messages(caplog)
    assert "duration_ms=1000 slow=True response_start_ms=250 first_body_ms=1000" in message
    assert "event_stream=False failed=False phase=response" in message
