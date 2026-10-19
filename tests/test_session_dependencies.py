"""Core teardown contracts use synthetic resources, with no optional add-on imports."""
import httpx
import pytest

from evander.session_dependencies import SessionDependencies


def test_cleanup_requires_confirmation_and_never_forwards_guest_credentials(tmp_path, monkeypatch):
    registry = SessionDependencies(tmp_path)
    registry.register("agent-a", "preview-a", "https://private.test/resources/a/close", "CLEANUP_TOKEN")
    monkeypatch.setenv("CLEANUP_TOKEN", "host-only-cleanup-token")
    received = []
    def callback(request):
        received.append(request)
        return httpx.Response(200, json={"released": False})
    with pytest.raises(ValueError, match="preserved"):
        registry.release("agent-a", transport=httpx.MockTransport(callback))
    assert received[0].headers["authorization"] == "Bearer host-only-cleanup-token"
    assert received[0].url.path == "/resources/a/close"
    registry.release("another-agent", transport=httpx.MockTransport(lambda r: pytest.fail("Wrong owner")))
    registry.release("agent-a", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"released": True})))
    registry.release("agent-a", transport=httpx.MockTransport(lambda r: pytest.fail("Released dependency must be retired")))


def test_dependency_cleanup_survives_process_restart_and_missing_credentials(tmp_path):
    registry = SessionDependencies(tmp_path)
    registry.register("workspace:agent-a:/home/agent/worktrees/repo/task-a", "resource-a",
                      "https://private.test/resources/a/close", "ABSENT_CLEANUP_TOKEN")
    recovered = SessionDependencies(tmp_path)
    with pytest.raises(ValueError, match="credentials"):
        recovered.release("workspace:agent-a:/home/agent/worktrees/repo/task-a")
    assert recovered.path.stat().st_mode & 0o777 == 0o600
