import httpx
import pytest

from evander_browser_sessions_addon.models import BrowserError
from evander_browser_sessions_addon.service import HttpPeer


def test_peer_reauthorizes_before_every_request():
    calls, resolutions = [], []
    def credential():
        resolutions.append(True)
        if len(resolutions) > 1:
            raise BrowserError("Credential was disabled", "credential_unavailable", 409)
        return "authorized-token"
    peer = HttpPeer("https://worker.test/browser", credential, transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(200, json={})
    ))
    peer.call("GET", "/healthz")
    with pytest.raises(BrowserError, match="disabled"):
        peer.call("POST", "/v1/sessions/new", {})
    assert len(calls) == 1
    assert calls[0].headers["authorization"] == "Bearer authorized-token"


@pytest.mark.parametrize(
    "code", ["invalid_coordinates", "stale_observation", "stale_target", "stale_control", "disconnected",
             "unsupported_target", "compatibility_unsupported", "capture_unavailable"]
)
def test_browser_action_conflict_preserves_safe_code_without_peer_details(monkeypatch, code):
    peer = HttpPeer(
        "https://relay.test",
        "test-token",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(409, json={"code": code, "error": "server-only-secret"})
        ),
    )
    with pytest.raises(BrowserError) as caught:
        peer.call("POST", "/internal/sessions/synthetic/command", {})
    assert caught.value.status == 409
    assert caught.value.code == code
    assert "server-only-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "failure",
    [
        {"code": "server-only-secret", "error": "server-only-secret"},
        {"code": ["server-only-secret"]},
        ["server-only-secret"],
        "<html>server-only-secret</html>",
    ],
)
def test_unknown_or_malformed_conflicts_keep_generic_safe_response(monkeypatch, failure):
    peer = HttpPeer(
        "https://relay.test",
        "test-token",
        transport=httpx.MockTransport(
            lambda _: (
                httpx.Response(409, text=failure)
                if isinstance(failure, str)
                else httpx.Response(409, json=failure)
            )
        ),
    )
    with pytest.raises(BrowserError) as caught:
        peer.call("POST", "/internal/sessions/synthetic/command", {})
    assert caught.value.status == 409
    assert caught.value.code == "peer_conflict"
    assert "server-only-secret" not in str(caught.value)
