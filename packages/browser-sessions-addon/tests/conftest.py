import time
from types import SimpleNamespace

import pytest

from evander_browser_sessions_addon.models import Settings
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

    def http_targets(self, agent):
        return {
            "app": SimpleNamespace(base_url="http://preview-app:4200"),
            "other": SimpleNamespace(base_url="http://other-app:4200"),
        }

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
                base, token() if callable(token) else token, method, path, payload
            )
        )

    def call(self, base, token, method, path, payload):
        self.calls.append((base, token, method, path, payload))
        if method == "DELETE" and self.fail_delete:
            from evander_browser_sessions_addon.models import BrowserError

            raise BrowserError("cleanup failed")
        if path == "/healthz":
            return {"capabilities": {"browser_sessions": 1}}
        if path.endswith("/fence"):
            return {"fenced": True}
        if method == "POST" and path.startswith("/v1/sessions"):
            return {"allowed_origins": payload["session"]["allowed_origins"], "state": "accepted"}
        return {"released": True, "state": "running", "success": True}


@pytest.fixture
def domain(tmp_path, monkeypatch):
    from evander_browser_sessions_addon.credentials import Credentials
    key = tmp_path.parent / (tmp_path.name + ".key")
    monkeypatch.setenv("EVANDER_CREDENTIALS_KEY_FILE", str(key))
    credentials = Credentials(tmp_path)
    credentials.store.initialize()
    store = Store(tmp_path)
    settings = Settings(
        enabled=True,
        public_base_url="https://control.test",
        workers={
            "worker-a": {
                "base_url": "https://worker.test/browser",
                "parent_worker_url": "https://worker.test",
            }
        },
        profiles={"web": {"image": "seeded-web:1"}},
    )
    settings.workers["worker-a"].credential_ref = credentials.create(
        "root", "worker", settings.workers["worker-a"].base_url, "fixture-worker-token",
        worker_id="worker-a", connection=settings.workers["worker-a"],
    )["id"]
    settings.relay_credential_ref = credentials.create(
        "root", "relay", settings.relay_url, "fixture-relay-token"
    )["id"]
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
            mode="hosted",
            request_id="create",
            profile_id="web",
            target_aliases=["app"],
            entry_url="http://preview-app:4200",
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


@pytest.fixture
def speech_provider(domain, monkeypatch):
    import io
    import wave
    import httpx
    from evander_browser_sessions_addon.models import VoiceSettings
    from evander_browser_sessions_addon.voice import Voice

    service, _, _ = domain
    revision, settings = service.store.settings("root")
    settings.voice = VoiceSettings(enabled=True, api_base_url="https://speech.test/v1")
    service.store.save_settings("root", revision, settings)
    settings.voice.credential_ref = service.credentials.create(
        "root", "voice", settings.voice.api_base_url, "fixture-speech-secret"
    )["id"]
    service.store.save_settings("root", service.store.settings("root")[0], settings)
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 2400)
    state = {"calls": [], "status": 200, "after": None, "audio": output.getvalue()}

    async def respond(request):
        state["calls"].append(request)
        assert request.headers["authorization"] == "Bearer fixture-speech-secret"
        if state["after"]:
            state["after"]()
        if state["status"] != 200:
            return httpx.Response(state["status"], text="provider failure fixture-speech-secret")
        if request.url.path.endswith("/transcriptions"):
            return httpx.Response(200, json={"text": "Move the button to the left."})
        return httpx.Response(200, content=output.getvalue(), headers={"Content-Type": "audio/wav"})

    original = Voice.provider

    async def provider(self, *args, **kwargs):
        self.transport = httpx.MockTransport(respond)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(Voice, "provider", provider)
    return state
