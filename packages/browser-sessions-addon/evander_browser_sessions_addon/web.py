"""Authenticated hosted viewer. Guest sharing never traverses these endpoints."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import os
from pathlib import Path

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from evander.admin.web.rbac import (
    BROWSER_SESSIONS_VIEW_PERMISSION as VIEW,
    BROWSER_SESSIONS_CONTROL_PERMISSION as CONTROL,
    BROWSER_SESSIONS_MANAGE_PERMISSION as MANAGE,
)
from evander.models import DirectoryPath

from .models import BrowserCommand, BrowserError, CreateRequest, MessageRequest, Settings, origin
from .relay import error, read_json
from .service import Service
from .voice import Voice, read_audio, require_voice_origin


def authenticated(context, connection):
    if not context.auth.config.auth_required:
        raise BrowserError(
            "Configure Admin UI sign-in before opening hosted browsers",
            "sign_in_not_configured",
            409,
        )
    session = context.auth.read_session(connection)
    if session is None:
        raise BrowserError("Sign in to open this browser session", "authentication_required", 401)
    return session


def authorize(context, connection, directory, permission):
    session = authenticated(context, connection)
    context.auth.require_permission(session, permission, directory_path=directory)
    return "user:" + str(session["login"])


def mutation(request, settings):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise BrowserError("JSON content type is required", "forbidden", 403)
    supplied = request.headers.get("origin")
    expected = settings.public_base_url or str(request.base_url)
    if supplied and origin(supplied) != origin(expected):
        raise BrowserError("Request origin does not match the control plane", "forbidden", 403)


def admin_api_routes(context):
    service = Service(context.runtime_dir, context.service)
    voice = Voice(service)

    async def settings(request):
        try:
            directory = DirectoryPath(
                segments=request.query_params.get("directory", "root")
            ).as_posix()
            authorize(context, request, directory, MANAGE)
            revision, value = service.settings(directory, required=False)
            if request.method == "PUT":
                mutation(request, value)
                payload = await read_json(request)
                if type(payload.get("expected_revision")) is not int:
                    raise BrowserError("expected_revision is required", "revision_required", 428)
                value = Settings.model_validate(payload["settings"])
                revision = service.store.save_settings(
                    directory, payload["expected_revision"], value
                )
            return JSONResponse(
                {"revision": revision, "settings": value.model_dump()},
                headers={"Cache-Control": "no-store"},
            )
        except PermissionError:
            return error(
                BrowserError("Browser-session management permission is required", "forbidden", 403)
            )
        except (BrowserError, ValidationError, ValueError, KeyError) as exc:
            return error(exc)

    async def sessions(request):
        try:
            authenticated(context, request)
            if request.method == "POST":
                payload = await read_json(request)
                parent = service.bindings.parent(str(payload["agent_session_id"]))
                actor = authorize(context, request, parent.directory, MANAGE)
                _, config = service.settings(parent.directory)
                mutation(request, config)
                create = CreateRequest.model_validate(payload["request"])
                result = await run_in_threadpool(
                    service.create, parent.session_id, create, tool=None
                )
                service.store.audit(result["id"], actor, "operator_created", {})
                return JSONResponse(result, status_code=201)
            directory = DirectoryPath(
                segments=request.query_params.get("directory", "root")
            ).as_posix()
            authorize(context, request, directory, VIEW)
            return JSONResponse(
                {
                    "sessions": [
                        service.receipt(item)
                        for item in service.store.list(directory)
                        if item.mode != "user_hosted"
                    ]
                },
                headers={"Cache-Control": "no-store"},
            )
        except PermissionError:
            return error(BrowserError("Browser-session permission is required", "forbidden", 403))
        except (BrowserError, ValidationError, ValueError, KeyError) as exc:
            return error(exc)

    async def operation(request):
        try:
            authenticated(context, request)
            session = service.store.get(request.path_params["session_id"])
            if session.mode == "user_hosted":
                raise BrowserError("Local browser sharing is private to its host", "not_found", 404)
            action = request.path_params.get("action", "state")
            if request.method == "GET" and action not in {"state", "messages"}:
                raise BrowserError("This operation requires POST", "method_not_allowed", 405)
            permission = (
                MANAGE
                if action == "close"
                else CONTROL
                if request.method == "POST" and action != "extend"
                else VIEW
            )
            actor = authorize(context, request, session.directory, permission)
            if request.method == "POST":
                mutation(request, service.store.settings(session.directory)[1])
                payload = await read_json(request)
            if action == "state":
                result = service.receipt(session)
            elif action == "messages":
                if request.method == "POST":
                    result = service.enqueue_message(
                        session.id, actor, MessageRequest.model_validate(payload)
                    )
                else:
                    result = {
                        "messages": service.store.messages(
                            session.id, "viewer", int(request.query_params.get("after", 0))
                        )
                    }
            elif action == "control":
                result = await run_in_threadpool(
                    service.control,
                    session.id,
                    actor,
                    str(payload["action"]),
                    revision=payload.get("expected_revision"),
                    generation=payload.get("expected_generation"),
                )
            elif action == "command":
                command = BrowserCommand.model_validate(payload)
                result = await run_in_threadpool(service.command, session.id, actor, command)
            elif action == "screenshot":
                result = await run_in_threadpool(
                    service.save_screenshot, session.id, actor, int(payload["generation"])
                )
            elif action == "close":
                result = await run_in_threadpool(service.close, session.id, actor)
            elif action == "extend":
                if type(payload.get("expected_revision")) is not int:
                    raise BrowserError("expected_revision is required", "revision_required", 428)
                service.bindings.parent(session.agent_session_id)

                def extend(value):
                    service.store.active(value)
                    # Keep-alive resets inactivity; the hard worker deadline is immutable.
                    value.last_activity_at = service.now()

                result = service.receipt(
                    service.store.mutate(
                        session.id,
                        actor,
                        extend,
                        revision=payload["expected_revision"],
                        kind="extended",
                    )
                )
            else:
                raise BrowserError("Unknown browser operation", "not_found", 404)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except PermissionError:
            return error(BrowserError("Browser-session permission is required", "forbidden", 403))
        except (BrowserError, ValidationError, ValueError, KeyError) as exc:
            return error(exc)

    async def voice_operation(request):
        try:
            session = service.store.get(request.path_params["session_id"])
            if session.mode == "user_hosted":
                raise BrowserError("Local voice chat is private to its host", "not_found", 404)
            action = request.path_params["action"]
            permission = CONTROL if action == "transcribe" else VIEW
            actor = authorize(context, request, session.directory, permission)
            if request.method == "GET" and action == "info":
                return JSONResponse(voice.info(session.id), headers={"Cache-Control": "no-store"})
            config = service.store.settings(session.directory)[1]
            require_voice_origin(request, config)
            if request.method == "POST" and action == "transcribe":
                limits = voice.configured(session.id)
                audio, mime = await read_audio(request, limits.max_audio_bytes)
                result = await voice.transcribe(
                    session.id, actor, request.headers.get("x-voice-request-id", ""), audio, mime
                )
                authorize(context, request, session.directory, permission)
                return JSONResponse(result, headers={"Cache-Control": "no-store"})
            if request.method == "POST" and action == "speech":
                mutation(request, config)
                payload = await read_json(request)
                content = await voice.speech(
                    session.id, actor, str(payload["request_id"]), str(payload["message_id"])
                )
                authorize(context, request, session.directory, permission)
                return Response(content, media_type="audio/wav", headers={"Cache-Control": "no-store"})
            raise BrowserError("Unknown voice operation", "not_found", 404)
        except PermissionError:
            return error(BrowserError("Browser-session permission is required", "forbidden", 403))
        except (BrowserError, ValueError, KeyError, ValidationError) as exc:
            return error(exc)

    async def stream(websocket):
        inner = None
        connection = HTTPConnection(websocket.scope)
        try:
            session = service.store.get(websocket.path_params["session_id"])
            if session.mode == "user_hosted":
                raise BrowserError("Local browser has no hosted viewer", "forbidden", 403)
            actor = authorize(context, connection, session.directory, VIEW)
            config = service.store.settings(session.directory)[1]
            if not websocket.headers.get("origin") or origin(websocket.headers["origin"]) != origin(
                config.public_base_url
            ):
                raise BrowserError("Stream origin rejected", "forbidden", 403)
            private = service.store.private(session.id)
            relay_url = private.get("relay_url", config.relay_url)
            token = os.environ.get(private.get("relay_token_env", config.relay_token_env), "")
            if not token:
                raise BrowserError("Browser relay is unavailable", "relay_unavailable", 503)
            url = relay_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
            inner = await connect(
                url + "/internal/sessions/" + session.id + "/view",
                additional_headers={"Authorization": "Bearer " + token},
                max_size=4 * 1024 * 1024,
                proxy=None,
            )
            await websocket.accept()
            last_revision = -1
            receive_task = asyncio.create_task(inner.recv())
            try:
                while True:
                    # Re-read the cookie's signed session and directory permissions throughout streaming.
                    await run_in_threadpool(authorize, context, connection, session.directory, VIEW)
                    current = service.store.get(session.id)
                    service.store.active(current)
                    if current.revision != last_revision:
                        await websocket.send_json(
                            {"kind": "state", "session": service.receipt(current), "actor": actor}
                        )
                        last_revision = current.revision
                    done, _ = await asyncio.wait({receive_task}, timeout=1)
                    if done:
                        payload = receive_task.result()
                        await websocket.send_text(
                            payload if isinstance(payload, str) else payload.decode()
                        )
                        receive_task = asyncio.create_task(inner.recv())
            finally:
                receive_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await receive_task
        except (
            BrowserError,
            PermissionError,
            WebSocketDisconnect,
            ConnectionClosed,
            OSError,
            RuntimeError,
        ):
            with suppress(Exception):
                await websocket.close(code=1008)
        finally:
            if inner is not None:
                await inner.close()

    return [
        Route("/settings", settings, methods=["GET", "PUT"]),
        Route("/sessions", sessions, methods=["GET", "POST"]),
        Route("/sessions/{session_id}", operation),
        Route("/sessions/{session_id}/{action}", operation, methods=["GET", "POST"]),
        Route("/sessions/{session_id}/voice/{action}", voice_operation, methods=["GET", "POST"]),
        WebSocketRoute("/sessions/{session_id}/stream", stream),
    ]


def admin_ui_routes(context):
    root = Path(__file__).parent / "ui_static"

    async def index(request):
        try:
            authenticated(context, request)
            return FileResponse(
                root / "index.html",
                headers={
                    "Cache-Control": "no-store",
                    "Content-Security-Policy": "default-src 'self'; connect-src 'self'; media-src blob:; img-src 'self' blob: data:; style-src 'self'; base-uri 'none'; frame-ancestors 'self'",
                    "Referrer-Policy": "no-referrer",
                },
            )
        except BrowserError as exc:
            return error(exc)

    async def asset(request):
        name = request.path_params["name"]
        if name not in {"viewer.js", "styles.css"}:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(root / name)

    return [Route("/", index), Route("/view/{session_id}", index), Route("/assets/{name}", asset)]
