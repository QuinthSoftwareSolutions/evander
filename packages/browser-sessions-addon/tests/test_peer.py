import httpx
import pytest

from evander_browser_sessions_addon.models import BrowserError
from evander_browser_sessions_addon.service import HttpPeer


@pytest.mark.parametrize(
    "code", ["invalid_coordinates", "stale_observation", "stale_control", "disconnected"]
)
def test_browser_action_conflict_preserves_safe_code_without_peer_details(monkeypatch, code):
    monkeypatch.setenv("BROWSER_TEST_TOKEN", "test-token")
    peer = HttpPeer(
        "https://relay.test",
        "BROWSER_TEST_TOKEN",
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
    monkeypatch.setenv("BROWSER_TEST_TOKEN", "test-token")
    peer = HttpPeer(
        "https://relay.test",
        "BROWSER_TEST_TOKEN",
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
