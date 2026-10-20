"""TLS-fronted guest bridge and private hosted-frame broker. No frame archive."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import json
import os
from pathlib import Path

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect
import uvicorn

from .models import BrowserCommand, BrowserError, Frame, MessageRequest, TERMINAL, origin
from .protocol import authorize_command
from .service import Service
from .voice import Voice, read_audio, require_voice_origin


def bearer(headers):
    value = headers.get("authorization", "")
    return value[7:] if value.startswith("Bearer ") else ""


def require_internal(connection, token):
    supplied = bearer(connection.headers)
    if not token or not supplied or not hmac.compare_digest(supplied, token):
        raise BrowserError("Authentication required", "unauthorized", 401)


async def read_json(request, limit=65536):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise BrowserError("JSON content type is required", "unsupported_media_type", 415)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise BrowserError("Request is too large", "request_too_large", 413)
    value = json.loads(body)
    if not isinstance(value, dict):
        raise BrowserError("Expected a JSON object")
    return value


def error(exc):
    if isinstance(exc, BrowserError):
        return JSONResponse({"error": str(exc), "code": exc.code}, status_code=exc.status)
    return JSONResponse(
        {"error": "Invalid browser request", "code": "invalid_request"}, status_code=400
    )


class Broker:
    def __init__(self, service: Service):
        self.service, self.store = service, service.store
        self.endpoints = {}
        self.watchers = {}
        self.pending = {}
        self.latest = {}

    async def publish(self, session_id, payload):
        for queue in list(self.watchers.get(session_id, ())):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(payload)

    async def execute(self, session_id, actor, command):
        session = self.store.get(session_id)
        self.store.active(session)
        authorize_command(session, actor, command, agent=actor.startswith("agent:"))
        endpoint = self.endpoints.get(session_id)
        if endpoint is None:
            raise BrowserError(
                "Browser is disconnected; reconnect and observe", "disconnected", 409
            )
        future = asyncio.get_running_loop().create_future()
        key = (session_id, command.id)
        if key in self.pending:
            raise BrowserError("Command is already pending", "command_duplicate", 409)
        self.pending[key] = future
        try:
            await endpoint.send_json({"kind": "state", "session": self.service.receipt(session)})
            # The endpoint receives authoritative control state before each command.
            await endpoint.send_json(
                {
                    "kind": "command",
                    "version": 1,
                    "session_id": session_id,
                    "controller": session.controller,
                    "actor": actor,
                    "allowed_origins": session.allowed_origins,
                    "expires_at": session.expires_at,
                    "command": command.model_dump(),
                }
            )
            return await asyncio.wait_for(future, 30)
        except (TimeoutError, WebSocketDisconnect, RuntimeError):
            raise BrowserError(
                "Browser action outcome is unknown; observe before retrying", "outcome_unknown", 409
            ) from None
        finally:
            self.pending.pop(key, None)

    async def endpoint(self, websocket, role):
        await websocket.accept()
        session_id = None
        try:
            hello = await asyncio.wait_for(websocket.receive_json(), 10)
            if hello.get("kind") != "authenticate" or hello.get("version") != 1:
                raise BrowserError("Incompatible browser protocol")
            capability = self.store.authenticate(str(hello.get("token", "")), role)
            session_id = capability["session_id"]
            session = self.store.get(session_id)
            supplied_origin = websocket.headers.get("origin", "")
            if role == "guest_endpoint" and not supplied_origin.startswith("chrome-extension://"):
                raise BrowserError(
                    "Only the Chrome extension may host this endpoint", "forbidden", 403
                )
            if role == "hosted_endpoint" and supplied_origin:
                raise BrowserError(
                    "Hosted worker endpoint rejects browser-origin connections", "forbidden", 403
                )
            if (role == "guest_endpoint") != (session.mode == "user_hosted"):
                raise BrowserError("Connection role mismatch", "forbidden", 403)
            if role == "guest_endpoint" and capability["principal"] != session.host_principal:
                raise BrowserError("Local browser owner changed", "forbidden", 403)
            old = self.endpoints.get(session_id)
            if old:
                await old.close(code=1012)
            self.endpoints[session_id] = websocket
            if session.state == "disconnected":

                def reconnect(value):
                    self.store.active(value)
                    value.state, value.controller = "paused", None
                    value.control_generation += 1

                session = self.store.mutate(session_id, "system", reconnect, kind="reconnected")
            await websocket.send_json({"kind": "state", "session": self.service.receipt(session)})
            while True:
                payload = await asyncio.wait_for(websocket.receive_json(), 45)
                self.store.authenticate(str(hello.get("token", "")), role)
                session = self.store.get(session_id)
                if payload.get("kind") == "heartbeat":
                    await websocket.send_json(
                        {"kind": "state", "session": self.service.receipt(session)}
                    )
                elif payload.get("kind") == "frame":
                    frame = Frame.model_validate(payload)
                    if origin(frame.url) not in session.allowed_origins:
                        # A navigation revokes control before any new-origin pixels leave the endpoint.
                        def pause(value):
                            self.store.active(value)
                            value.state, value.controller = "paused", None
                            value.control_generation += 1

                        self.store.mutate(session_id, "system", pause, kind="origin_paused")
                        await websocket.send_json({"kind": "pause", "reason": "origin_denied"})
                        continue
                    if session.state not in {"ready", "paused"}:
                        continue
                    if session.current_url != frame.url:
                        self.store.mutate(
                            session_id,
                            "system",
                            lambda value: setattr(value, "current_url", frame.url),
                            kind="navigated",
                        )
                    self.latest[session_id] = frame.model_dump()
                    await self.publish(session_id, frame.model_dump())
                elif payload.get("kind") == "result":
                    pending = self.pending.get((session_id, payload.get("id")))
                    if pending and not pending.done():
                        if payload.get("error"):
                            pending.set_exception(
                                BrowserError(
                                    "Browser rejected the action",
                                    str(payload.get("code", "browser_error")),
                                    409,
                                )
                            )
                        else:
                            pending.set_result(payload.get("result", {}))
                elif payload.get("kind") == "ready":
                    if (
                        session.allowed_origins
                        and origin(str(payload.get("url", ""))) in session.allowed_origins
                        and (
                            session.state == "starting"
                            or session.current_url != str(payload.get("url", ""))
                        )
                    ):

                        def ready(value):
                            self.store.active(value)
                            value.state = "ready" if value.controller else "paused"
                            value.current_url = str(payload["url"])

                        self.store.mutate(session_id, "system", ready, kind="ready")
                elif payload.get("kind") == "paused":

                    def paused(value):
                        self.store.active(value)
                        value.controller, value.state = None, "paused"
                        value.control_generation += 1

                    self.store.mutate(session_id, "system", paused, kind="local_pause")
                elif payload.get("kind") == "pointer":
                    x, y = payload.get("x"), payload.get("y")
                    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                        raise BrowserError("Invalid pointer")
                    await self.publish(
                        session_id,
                        {
                            "kind": "pointer",
                            "x": x,
                            "y": y,
                            "action": str(payload.get("action", "hover"))[:20],
                        },
                    )
                else:
                    raise BrowserError("Unknown browser protocol message")
        except (
            BrowserError,
            ValidationError,
            ValueError,
            TimeoutError,
            WebSocketDisconnect,
            RuntimeError,
        ):
            with suppress(Exception):
                await websocket.close(code=1008)
        finally:
            if session_id and self.endpoints.get(session_id) is websocket:
                self.endpoints.pop(session_id, None)
                self.latest.pop(session_id, None)
                session = self.store.get(session_id)
                if session.state in {"starting", "ready", "paused", "disconnected"}:

                    def disconnected(value):
                        if value.state not in {"starting", "ready", "paused", "disconnected"}:
                            return
                        value.state, value.controller = "disconnected", None
                        value.control_generation += 1

                    self.store.mutate(session_id, "system", disconnected, kind="disconnected")
                for (sid, _), pending in list(self.pending.items()):
                    if sid == session_id and not pending.done():
                        pending.set_exception(
                            BrowserError(
                                "Browser disconnected; action outcome is unknown",
                                "outcome_unknown",
                                409,
                            )
                        )

    async def view(self, websocket, internal_token):
        queue = None
        session_id = websocket.path_params["session_id"]
        try:
            require_internal(websocket, internal_token)
            session = self.store.get(session_id)
            self.store.active(session)
            if session.mode == "user_hosted":
                raise BrowserError("Local sessions have no public viewer", "forbidden", 403)
            await websocket.accept()
            queue = asyncio.Queue(maxsize=2)
            self.watchers.setdefault(session_id, set()).add(queue)
            if session_id in self.latest:
                queue.put_nowait(self.latest[session_id])
            while True:
                self.store.active(self.store.get(session_id))
                try:
                    frame = await asyncio.wait_for(queue.get(), 5)
                    await websocket.send_json(frame)
                except TimeoutError:
                    await websocket.send_json({"kind": "heartbeat"})
        except (BrowserError, WebSocketDisconnect, RuntimeError):
            with suppress(Exception):
                await websocket.close(code=1008)
        finally:
            if queue is not None:
                self.watchers.get(session_id, set()).discard(queue)


def create_app(runtime_dir, *, service=None, internal_token=None):
    service = service or Service(runtime_dir)
    broker = Broker(service)
    speech = Voice(service)
    internal_token = internal_token or os.environ.get("EVANDER_BROWSER_RELAY_TOKEN", "")

    async def tick(stop):
        while not stop.is_set():
            try:
                await asyncio.to_thread(service.reconcile)
                await asyncio.to_thread(service.dispatch_messages)
            except Exception:
                # A retry preserves durable intent; never log chat or browser credentials.
                pass
            try:
                await asyncio.wait_for(stop.wait(), 5)
            except TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        task = asyncio.create_task(tick(stop))
        yield
        stop.set()
        await task

    async def health(request):
        return JSONResponse({"ok": True, "capabilities": {"browser_sessions": 1}})

    async def pair(request):
        try:
            payload = await read_json(request)
            result = await asyncio.to_thread(
                service.pair,
                str(payload.get("invitation", "")),
                str(payload.get("application_url", "")),
            )
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except (BrowserError, ValueError, ValidationError) as exc:
            return error(exc)

    def host(request):
        capability = service.store.authenticate(bearer(request.headers), "guest_host")
        return capability, service.store.get(capability["session_id"])

    async def guest(request):
        try:
            capability, session = host(request)
            action = request.path_params["action"]
            if action == "state" and request.method == "GET":
                result = {
                    "session": service.receipt(session),
                    "messages": service.store.messages(
                        session.id, "viewer", int(request.query_params.get("after", 0))
                    ),
                }
            elif request.method == "POST" and action == "control":
                payload = await read_json(request)
                result = await asyncio.to_thread(
                    service.control,
                    session.id,
                    capability["principal"],
                    payload["action"],
                    revision=payload.get("expected_revision"),
                    generation=payload.get("expected_generation"),
                    host=True,
                )
                await broker.publish(session.id, {"kind": "state", "session": result})
            elif request.method == "POST" and action == "message":
                payload = MessageRequest.model_validate(await read_json(request))
                result = service.enqueue_message(session.id, capability["principal"], payload)
            elif request.method == "POST" and action == "voice":
                result = service.voice_invite(session.id, capability["principal"])
            elif request.method == "POST" and action == "origin":
                payload = await read_json(request)
                allowed = origin(payload["url"])

                def approve(value):
                    value.allowed_origins = [allowed]
                    value.current_url = str(payload["url"])
                    value.controller, value.state = None, "paused"
                    value.control_generation += 1
                    value.last_activity_at = service.now()

                result = service.receipt(
                    service.store.mutate(
                        session.id, capability["principal"], approve, kind="origin_approved"
                    )
                )
                endpoint = broker.endpoints.get(session.id)
                if endpoint is not None:
                    await endpoint.send_json({"kind": "state", "session": result})
            elif request.method == "POST" and action == "close":
                result = await asyncio.to_thread(service.close, session.id, capability["principal"])
            else:
                raise BrowserError("Unknown guest operation", "not_found", 404)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except (BrowserError, ValueError, KeyError, ValidationError) as exc:
            return error(exc)

    async def voice_guest(request):
        token = bearer(request.headers)
        try:
            capability = service.store.authenticate(token, "guest_voice")
            session = service.store.get(capability["session_id"])
            if session.mode != "user_hosted" or capability["principal"] != session.host_principal:
                raise BrowserError("Voice host changed", "forbidden", 403)
            actor, action = capability["principal"], request.path_params["action"]
            if request.method == "GET" and action == "info":
                result = speech.info(session.id)
            elif request.method == "GET" and action == "state":
                result = {
                    "session": service.receipt(session),
                    "messages": service.store.messages(
                        session.id, "viewer", int(request.query_params.get("after", 0))
                    ),
                }
            elif request.method == "POST":
                require_voice_origin(
                    request, service.store.settings(session.directory)[1], allow_extension=True
                )
                if action == "transcribe":
                    limits = speech.configured(session.id)
                    audio, mime = await read_audio(request, limits.max_audio_bytes)
                    result = await speech.transcribe(
                        session.id, actor, request.headers.get("x-voice-request-id", ""), audio, mime
                    )
                elif action in {"speech", "message"}:
                    if request.headers.get("content-type", "").split(";")[0] != "application/json":
                        raise BrowserError("JSON content type is required", "forbidden", 403)
                    payload = await read_json(request)
                    if action == "message":
                        result = service.enqueue_message(
                            session.id, actor, MessageRequest.model_validate(payload)
                        )
                    else:
                        content = await speech.speech(
                            session.id, actor, str(payload["request_id"]), str(payload["message_id"])
                        )
                        service.store.authenticate(token, "guest_voice")
                        return Response(content, media_type="audio/wav",
                                        headers={"Cache-Control": "no-store"})
                else:
                    raise BrowserError("Unknown voice operation", "not_found", 404)
                service.store.authenticate(token, "guest_voice")
            else:
                raise BrowserError("Unknown voice operation", "not_found", 404)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except (BrowserError, ValueError, KeyError, ValidationError) as exc:
            return error(exc)

    async def voice_page(request):
        return FileResponse(
            Path(__file__).parent / "ui_static" / "voice.html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; media-src blob:; connect-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "microphone=(self)",
            },
        )

    async def voice_asset(request):
        name = request.path_params["name"]
        if name not in {"voice.js", "voice.css"}:
            raise BrowserError("Unknown voice asset", "not_found", 404)
        return FileResponse(Path(__file__).parent / "ui_static" / name)

    async def internal(request):
        try:
            require_internal(request, internal_token)
            session_id = request.path_params["session_id"]
            if request.path_params["action"] == "sync":
                endpoint = broker.endpoints.get(session_id)
                if endpoint is not None:
                    await endpoint.send_json(
                        {"kind": "state", "session": service.receipt(service.store.get(session_id))}
                    )
                return JSONResponse({"synchronized": endpoint is not None})
            if request.path_params["action"] == "close":
                result = await asyncio.to_thread(service.close, session_id)
                return JSONResponse({"released": result["state"] in TERMINAL})
            if request.path_params["action"] != "command":
                raise BrowserError("Unknown relay operation", "not_found", 404)
            payload = await read_json(request)
            command = BrowserCommand.model_validate(payload["command"])
            result = await broker.execute(session_id, str(payload["actor"]), command)
            return JSONResponse(result)
        except (BrowserError, ValueError, KeyError, ValidationError) as exc:
            return error(exc)

    async def endpoint(websocket):
        await broker.endpoint(websocket, "hosted_endpoint")

    async def extension(websocket):
        await broker.endpoint(websocket, "guest_endpoint")

    async def view(websocket):
        await broker.view(websocket, internal_token)

    async def join(request):
        return FileResponse(
            Path(__file__).parent / "ui_static" / "join.html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
            },
        )

    async def extension_zip(request):
        return FileResponse(
            Path(__file__).parent / "ui_static" / "chrome-extension.zip",
            filename="evander-browser-assistance.zip",
            headers={"Cache-Control": "no-store"},
        )

    app = Starlette(
        routes=[
            Route("/healthz", health),
            Route("/_browser/guest/join", join),
            Route("/_browser/guest/voice", voice_page),
            Route("/_browser/voice-assets/{name}", voice_asset),
            Route("/_browser/guest/voice/{action}", voice_guest, methods=["GET", "POST"]),
            Route("/_browser/extension.zip", extension_zip),
            Route("/_browser/guest/pair", pair, methods=["POST"]),
            Route("/_browser/guest/{action}", guest, methods=["GET", "POST"]),
            WebSocketRoute("/_browser/endpoint", endpoint),
            WebSocketRoute("/_browser/extension", extension),
            WebSocketRoute("/internal/sessions/{session_id}/view", view),
            Route("/internal/sessions/{session_id}/{action}", internal, methods=["POST"]),
        ],
        lifespan=lifespan,
    )
    app.state.broker = broker
    return app


def main():
    parser = argparse.ArgumentParser(description="Run the Browser Sessions relay")
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11890)
    args = parser.parse_args()
    from evander.admin.composition import build_admin_runtime

    components = build_admin_runtime(args.runtime_dir)
    service = Service(args.runtime_dir, components)
    uvicorn.run(
        create_app(args.runtime_dir, service=service),
        host=args.host,
        port=args.port,
        ws_max_size=4 * 1024 * 1024,
        access_log=False,
    )
