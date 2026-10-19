import time
from types import SimpleNamespace

import pytest

from evander_browser_sessions_addon.models import Session, Settings
from evander_browser_sessions_addon.service import Parent, Service
from evander_browser_sessions_addon.state import Store


class Bindings:
    def __init__(self):
        self.prompts, self.closed, self.denied = [], [], set()

    def parent(self, agent, tool=None):
        if agent in self.denied:
            raise ValueError("owner removed")
        return Parent(
            agent, "root/demo", time.time() + 20000, "worker-a", "https://worker.test", "thread-123"
        )

    def prepare_support(self, session, parent, settings):
        return "support-" + session.id

    def close_support(self, session):
        self.closed.append(session.id)

    def enqueue(self, session, message):
        self.prompts.append((session.id, message))
        return True


class Peers:
    def __init__(self):
        self.calls, self.fail_delete = [], False

    def __call__(self, base, token):
        return SimpleNamespace(
            call=lambda method, path, payload=None, **kw: self.call(
                base, token, method, path, payload
            )
        )

    def call(self, base, token, method, path, payload):
        self.calls.append((base, token, method, path, payload))
        if method == "DELETE" and self.fail_delete:
            from evander_browser_sessions_addon.models import BrowserError

            raise BrowserError("cleanup failed")
        if path == "/healthz":
            return {"capabilities": {"browser_sessions": 1}}
        if method == "POST" and path.startswith("/v1/sessions"):
            return {"allowed_origins": ["http://preview-app:4200"], "state": "accepted"}
        return {"released": True, "state": "running", "success": True}


@pytest.fixture
def domain(tmp_path):
    store = Store(tmp_path)
    settings = Settings(
        enabled=True,
        public_base_url="https://control.test",
        workers={
            "worker-a": {
                "base_url": "https://worker.test/browser",
                "parent_worker_url": "https://worker.test",
                "token_env": "WORKER_TOKEN",
            }
        },
        profiles={"web": {"image": "seeded-web:1"}},
    )
    store.save_settings("root", 0, settings)
    bindings, peers = Bindings(), Peers()
    service = Service(tmp_path, store=store, bindings=bindings, peer_factory=peers)
    return service, bindings, peers


@pytest.fixture
def hosted(domain):
    from evander_browser_sessions_addon.models import CreateRequest

    service, _, _ = domain
    result = service.create(
        "agent-a",
        CreateRequest(
            mode="hosted_development",
            request_id="create",
            profile_id="web",
            workspace_ref="a" * 32,
            launch_manifest_path=".evander/preview.json",
        ),
    )
    return service.store.mutate(result["id"], "test", lambda s: setattr(s, "state", "ready"))


@pytest.fixture
def local(domain):
    from evander_browser_sessions_addon.models import CreateRequest

    service, _, _ = domain
    return service.store.get(
        service.create("agent-a", CreateRequest(mode="user_hosted", request_id="local"))["id"]
    )
