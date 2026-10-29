"""Bounded multipart parsing; image bytes never enter query JSON or diagnostics."""

from __future__ import annotations

import json

from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import Request

from evander.voice_visuals import MAX_IMAGE_BYTES, MAX_REQUEST_BYTES


async def read_voice_query_request(request: Request) -> tuple[dict, list | None]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != "multipart/form-data":
        return await request.json(), None
    body = bytearray()
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_REQUEST_BYTES:
            raise ValueError("Visual query exceeds the request size limit")
        body.extend(chunk)

    async def receive():
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    bounded_request = Request(request.scope, receive)
    try:
        async with bounded_request.form(max_files=2, max_fields=1, max_part_size=64 * 1024) as form:
            return await _read_visual_parts(form)
    except HTTPException as exc:
        raise ValueError("Invalid visual query multipart body") from exc


async def _read_visual_parts(form):
    raw_payload = form.get("payload")
    if not isinstance(raw_payload, str):
        raise ValueError("Visual query requires a JSON payload part")
    payload = json.loads(raw_payload)
    if not isinstance(payload, dict) or not isinstance(payload.get("images"), list):
        raise ValueError("Visual query requires snapshot metadata")
    descriptions = payload.pop("images")
    if not 1 <= len(descriptions) <= 2:
        raise ValueError("Visual query requires one or two snapshots")
    sources = [
        description.get("source") for description in descriptions if isinstance(description, dict)
    ]
    if any(source not in ("camera", "screen") for source in sources) or len(set(sources)) != len(
        descriptions
    ):
        raise ValueError("Invalid visual snapshot sources")
    if set(form) != {"payload", *sources}:
        raise ValueError("Visual parts must match snapshot sources")
    images = []
    for description in descriptions:
        parts = form.getlist(description["source"])
        if (
            len(parts) != 1
            or not isinstance(parts[0], UploadFile)
            or parts[0].content_type != "image/jpeg"
        ):
            raise ValueError("Each snapshot requires one JPEG file part")
        content = await parts[0].read(MAX_IMAGE_BYTES + 1)
        if len(content) > MAX_IMAGE_BYTES:
            raise ValueError("Snapshot exceeds the 512 KiB image limit")
        images.append((description, content))
    return payload, images
