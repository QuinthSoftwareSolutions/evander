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


def test_unmigrated_browser_callback_is_blocked_without_reading_environment(tmp_path, monkeypatch):
    registry = SessionDependencies(tmp_path)
    registry.register("agent", "browser-legacy", "https://collector.test/close", "SESSION_SECRET")
    monkeypatch.setenv("SESSION_SECRET", "must-not-leave-host")
    with pytest.raises(ValueError, match="preserved"):
        registry.release("agent", transport=httpx.MockTransport(lambda request: pytest.fail("Legacy credential sent")))
    # Trusted worker-side cleanup still uses its operator-owned environment.
    registry.remove("browser-legacy")
    registry.register("agent", "browser-worker:worker", "https://worker.test/v1/sessions/browser-worker/close", "SESSION_SECRET")
    received = []
    registry.release("agent", transport=httpx.MockTransport(lambda request: received.append(request) or httpx.Response(200, json={"released": True})))
    assert received[0].headers["authorization"] == "Bearer must-not-leave-host"


@pytest.mark.parametrize("owner", ["resource:preview-resource", "agent-session"])
def test_worker_target_cleanup_requires_matching_resource_owner(tmp_path, monkeypatch, owner):
    registry = SessionDependencies(tmp_path)
    registry.register(
        owner, "browser-preview:target:endpoint-a",
        "https://worker.test/v1/sessions/browser-preview/detach-preview-resource",
        "WORKER_CLEANUP_TOKEN",
    )
    monkeypatch.setenv("WORKER_CLEANUP_TOKEN", "worker-only-token")
    requests = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json={"released": True})
    )
    if owner == "agent-session":
        with pytest.raises(ValueError, match="preserved"):
            registry.release(owner, transport=transport)
        assert requests == []
    else:
        registry.release(owner, transport=transport)
        assert len(requests) == 1
        assert requests[0].headers["authorization"] == "Bearer worker-only-token"
        assert requests[0].url.path.endswith("/detach-preview-resource")
        registry.release(owner, transport=httpx.MockTransport(
            lambda request: pytest.fail("Released target callback was retained")
        ))
