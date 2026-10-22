"""Synthetic real-domain control bridge for the hosted Chromium regression."""

import json
import sys
import tempfile
from types import SimpleNamespace
import httpx
from evander_browser_sessions_addon.models import BrowserError, Session, Settings
from evander_browser_sessions_addon.service import Service
from evander_browser_sessions_addon.state import Store

state = json.loads(sys.argv[1])
controller_url = sys.argv[2]
with tempfile.TemporaryDirectory(prefix="evander-control-service-") as runtime:
    store = Store(runtime)
    settings = Settings(enabled=True, public_base_url="https://control.test",
                        profiles={"web": {"image": "seeded-web:1", "playwright_enabled": False}})
    store.save_settings("root", 0, settings)
    session = Session(**state, directory="root", mode="hosted", profile_id="web",
                      created_at=state["expires_at"]-60, last_activity_at=state["expires_at"]-60,
                      idle_seconds=300, state="ready")
    session, _ = store.create(session, "hosted-regression", 1)
    store.set_private(session.id, {"profile": settings.profiles["web"].model_dump(),
                                  "connection": {"base_url": "https://worker.test", "token_env": "WORKER_TOKEN"}})
    calls = []

    def peer(base, token):
        def call(method, path, payload=None, **kwargs):
            calls.append(path)
            if path.endswith("/sync"):
                raise BrowserError("Synthetic relay sync outage", "peer_unavailable", 503)
            if path.endswith("/fence"):
                with httpx.Client(timeout=12, trust_env=False) as client:
                    response = client.post(controller_url+"/control", json=payload,
                                           headers={"Authorization": "Bearer worker-proof"})
                    response.raise_for_status()
                    return response.json()
            raise AssertionError("Unexpected fixture operation")
        return SimpleNamespace(call=call)

    service = Service(runtime, store=store, bindings=SimpleNamespace(), peer_factory=peer)
    claimed = service.control(session.id, "user:operator", "claim")
    print(json.dumps({"session": claimed, "calls": calls}))
