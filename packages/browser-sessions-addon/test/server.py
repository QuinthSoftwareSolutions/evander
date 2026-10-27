"""Loopback-only integration fixture; never installed or used by production."""

import asyncio
import json
import io
import wave
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route, Mount
import uvicorn
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from evander.admin.web.shared import AdminWebAuth, AdminWebConfig
from evander.admin.web.user_store import AdminWebUserStore
from evander.admin.web.app import AuthMiddleware
from evander.classification.models import ClassificationSettings
from evander_browser_sessions_addon.models import (
    Session,
    Settings,
    BrowserCommand,
    BrowserError,
    RunRequest,
)
from evander_browser_sessions_addon.service import Service, Parent
from evander_browser_sessions_addon.relay import create_app as relay_app, error
from evander_browser_sessions_addon import web

folder = Path(sys.argv[1])
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
port = sock.getsockname()[1]
base = "http://127.0.0.1:" + str(port)
os.environ.update(
    EVANDER_BROWSER_RELAY_TOKEN="fixture-relay-token",
    EVANDER_BROWSER_FIXTURE_WORKER_TOKEN="fixture-worker-token",
    EVANDER_ADMIN_WEB_AUTH_MODE="local_password",
    EVANDER_ADMIN_WEB_USERNAME="operator",
    EVANDER_ADMIN_WEB_PASSWORD="fixture-password",
    EVANDER_ADMIN_WEB_COOKIE_SECURE="false",
    EVANDER_ADMIN_WEB_SESSION_SECRET="fixture-session-signing-secret",
    EVANDER_BROWSER_VOICE_API_KEY="fixture-speech-secret",
)


class FixtureClassifier:
    secret_resolver = staticmethod(lambda _: "synthetic-decision-key")

    def evaluate(self, settings, payload, **kwargs):
        state = payload["state"]
        controls = state["page"]["elements"]
        field = next((item for item in controls if item["role"] == "textbox"), None)
        button = next((item for item in controls if item["role"] == "button"), None)
        value = state.get("inputs", {}).get("v0", {}).get("value", "")
        if state["goal"] == "Wait until stopped":
            time.sleep(1)
            action = "wait"
        elif field and field.get("value") != value:
            action = "fill"
        elif value + " added" not in state["page"].get("text", ""):
            action = "click"
        else:
            action = "done"
        selected = {
            "action": action,
            "target": field["ref"] if action == "fill" else button["ref"] if button else "_none",
            "input": "v0",
            "key": "Enter",
            "direction": "down",
        }
        answers = {}
        for name, question in payload["questions"].items():
            choice = selected[name]
            if choice not in question["criteria"]:
                choice = next(iter(question["criteria"]))
            answers[name] = {
                "type": "choice",
                "choice": choice,
                "probabilities": {key: int(key == choice) for key in question["criteria"]},
            }
        return {"answers": answers}


class Bindings:
    def classifier(self, directory):
        return FixtureClassifier(), ClassificationSettings(enabled=True)

    def parent(self, agent, tool=None):
        return Parent(agent, "root/demo", time.time() + 14400)

    def close_support(self, session):
        pass

    def enqueue(self, session, message):
        return True


os.environ["EVANDER_CREDENTIALS_KEY_FILE"] = str(folder / "master.key")
service = Service(folder / "runtime", bindings=Bindings())
service.credentials.store.initialize()
relay_ref = service.credentials.create("root", "relay", base, "fixture-relay-token")["id"]
voice_ref = service.credentials.create("root", "voice", base + "/test/provider", "fixture-speech-secret")["id"]
worker = {"base_url": base, "parent_worker_url": base, "credential_ref": None}
worker["credential_ref"] = service.credentials.create("root", "worker", base, "fixture-worker-token", worker_id="fixture-worker", connection=worker)["id"]
service.store.save_settings(
    "root",
    0,
    Settings(
        enabled=True,
        public_base_url=base,
        relay_url=base,
        relay_credential_ref=relay_ref,
        fast_execution={"enabled": True},
        voice={"enabled": True, "api_base_url": base + "/test/provider", "credential_ref": voice_ref},
    ),
)
auth = AdminWebAuth(AdminWebConfig.from_env(), AdminWebUserStore(folder / "users.db"))
context = SimpleNamespace(auth=auth, runtime_dir=folder, service=None)
web.Service = lambda *args, **kwargs: service


async def setup(request):
    global voice_transcript
    payload = await request.json()
    voice_transcript = payload.get("voice_transcript", "Move the button to the left.")
    if "voice_seconds" in payload:
        revision, settings = service.store.settings("root")
        settings.voice.max_recording_seconds = payload["voice_seconds"]
        service.store.save_settings("root", revision, settings)
    local = payload.get("local", False)
    session = Session(
        id="browser-" + os.urandom(12).hex(),
        directory="root/demo",
        agent_session_id="agent-a",
        mode="user_hosted" if local else "hosted",
        worker_id=None if local else "fixture-worker",
        title="Shared station preview",
        state="starting",
        controller=None if local else "agent:agent-a",
        allowed_origins=[base],
        created_at=time.time(),
        expires_at=time.time() + 600,
        last_activity_at=time.time(),
    )
    service.store.create(session, session.id, 20)
    result = {
        "session": session.model_dump(),
        "base": base,
        "entry_url": base + "/application",
        "relay_url": base + "/_browser/endpoint",
        "allowed_origins": [base],
    }
    if local:
        result.update(service.invite(session.id, "agent:agent-a"))
    else:
        result["endpoint_token"] = service.store.capability(
            session.id, "hosted_endpoint", "worker:test", 600
        )
        with socket.socket() as control_probe:
            control_probe.bind(("127.0.0.1", 0))
            result["control_port"] = control_probe.getsockname()[1]
        service.store.set_private(session.id, {
            "profile": {"playwright_enabled": False},
            "connection": worker,
            "relay_url": base, "relay_credential_ref": relay_ref,
            "controller_port": result["control_port"],
            "controller_proof": result["endpoint_token"],
        })
    return JSONResponse(result)


async def worker_fence(request):
    if request.headers.get("authorization") != "Bearer fixture-worker-token":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    private = service.store.private(request.path_params["id"])
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        response = await client.post(
            f"http://127.0.0.1:{private['controller_port']}/control",
            json=await request.json(),
            headers={"Authorization": "Bearer " + private["controller_proof"]},
        )
    return JSONResponse(response.json(), status_code=response.status_code)


async def test_operation(request):
    try:
        return await perform_test_operation(request)
    except BrowserError as exc:
        return error(exc)


async def perform_test_operation(request):
    sid, action = request.path_params["id"], request.path_params["action"]
    session = service.store.get(sid)
    if action == "state":
        return JSONResponse(service.receipt(session))
    if action == "messages":
        return JSONResponse({"messages": service.store.messages(sid)})
    payload = await request.json()
    if action == "command":
        command = BrowserCommand.model_validate(payload)
        tool = "browser_session_observe" if command.observing else "browser_session_act"
        session = service.agent_session("agent-a", sid, tool)
        actor = "agent:" + session.agent_session_id
        result = await asyncio.to_thread(service.command, sid, actor, command, agent=True)
    elif action == "run":
        result = await asyncio.to_thread(
            service.run, "agent-a", sid, RunRequest.model_validate(payload)
        )
    elif action == "crash-batch":
        assert payload["phase"] in {"before_send", "after_send", "after_commit"}
        child = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "test/crash-batch.py", str(service.runtime_dir), sid, payload["phase"]],
            input=json.dumps(payload["request"]), text=True, capture_output=True, timeout=8,
        )
        result = {"exit_code": child.returncode, "error": child.stderr[-1000:]}
    elif action == "control":
        result = await asyncio.to_thread(service.control, sid, "user:operator", payload["action"])
    elif action == "reply":
        result = service.reply(
            session.agent_session_id, sid, payload["message_id"], payload["body"]
        )
    elif action == "style":
        (folder / "position.txt").write_text(str(payload["left"]))
        result = {"ok": True}
    else:
        result = await asyncio.to_thread(service.close, sid)
    return JSONResponse(result)


async def application(request):
    if request.cookies.get("demo_session") != "fixture":
        return HTMLResponse("<h1>Application login required</h1>", status_code=401)
    return HTMLResponse("""<!doctype html><html><head><title>Station fixture</title>
    <style>body{font:18px sans-serif;margin:32px;background:#f6f8fa}button{padding:14px;margin-left:12px}input{padding:10px}</style></head>
    <body><h1>Station materials</h1><label>Material <input aria-label="Material"></label>
    <button>Add material</button><p id="result"></p>
    <a href="/position" target="_blank">Open shared tab</a><script>
    document.querySelector("button").onclick=()=>document.querySelector("#result").textContent=document.querySelector("input").value+" added";
    setInterval(async()=>{const r=await fetch("/position");document.querySelector("button").style.marginLeft=(await r.text())+"px"},150);
    </script></body></html>""")


async def position(request):
    file = folder / "position.txt"
    return Response(file.read_text() if file.exists() else "12")


async def login(request):
    response = JSONResponse({"ok": True})
    response.set_cookie(auth.config.cookie_name, auth.login("operator", "fixture-password"))
    return response


async def unlock(request):
    auth.require(request)
    auth.mark_privileged_for_request(request)
    return JSONResponse({"ok": True})


voice_events = []
voice_transcript = "Move the button to the left."
audio_output = io.BytesIO()
with wave.open(audio_output, "wb") as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(24000)
    audio.writeframes(b"\0\0" * 72000)
voice_audio = audio_output.getvalue()


async def speech_provider(request):
    if request.headers.get("authorization") != "Bearer fixture-speech-secret":
        return JSONResponse({"error": "invalid fixture credential"}, status_code=401)
    if request.path_params["action"] == "transcriptions":
        content = await request.body()
        voice_events.append(
            {
                "action": "transcribe",
                "bytes": len(content),
                "wav": b'filename="speech.wav"' in content,
            }
        )
        return JSONResponse({"text": voice_transcript})
    payload = await request.json()
    voice_events.append({"action": "speech", "text": payload["input"], "voice": payload["voice"]})
    if payload.get("response_format") == "pcm":

        async def chunks():
            raw = b"\0\0" * 72000
            for offset in range(0, len(raw), 4800):
                yield raw[offset : offset + 4800]
                await asyncio.sleep(0.02)

        return StreamingResponse(chunks(), media_type="audio/pcm")
    return Response(voice_audio, media_type="audio/wav")


async def speech_events(request):
    return JSONResponse({"events": voice_events})


relay = relay_app(folder, service=service, internal_token="fixture-relay-token")


@asynccontextmanager
async def lifespan(app):
    # Mounted Starlette apps do not start their own lifespan automatically.
    async with relay.router.lifespan_context(relay):
        yield


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/test/voice/events", speech_events),
        Route("/test/provider/audio/{action}", speech_provider, methods=["POST"]),
        Route("/test/setup", setup, methods=["POST"]),
        Route("/test/unlock", unlock, methods=["POST"]),
        Route("/v1/sessions/{id}/fence", worker_fence, methods=["POST"]),
        Route("/test/{id}/{action}", test_operation, methods=["GET", "POST"]),
        Route("/application", application),
        Route("/position", position),
        Route("/api/auth/login", login, methods=["POST"]),
        Mount("/api/addons/browser-sessions-addon", routes=web.admin_api_routes(context)),
        Mount("/addons/browser-sessions-addon", routes=web.admin_ui_routes(context)),
        Mount("/", app=relay),
    ]
)
# The /test surface is a loopback fixture. Production Admin routes retain auth.
app.add_middleware(AuthMiddleware, auth=auth)
print(json.dumps({"port": port, "base": base}), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False)).run(sockets=[sock])
