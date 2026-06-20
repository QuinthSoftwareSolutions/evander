from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from agent_sandbox.addons import addon_storage_path
from agent_sandbox.models import utc_now

from .models import AgentAvatar, PersistentAgent, TeamState

ADDON_ID = "team-addon"
AVATAR_IMAGE_SIZE_PX = 512
MAX_AVATAR_UPLOAD_BYTES = 5 * 1024 * 1024
DEFAULT_AVATAR_MODEL = "gpt-image-2"
DEFAULT_AVATAR_STYLE = "professional"
DEFAULT_AVATAR_QUALITY = "low"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
AVATAR_MIME_TYPE = "image/webp"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")

STYLE_PRESETS: dict[str, str] = {
    "professional": (
        "a crisp professional illustrated head-and-shoulders avatar, subtle technical "
        "details, clean background, friendly but focused expression"
    ),
    "playful": (
        "a bright playful illustrated head-and-shoulders avatar, expressive shape language, "
        "clean background, distinctive but work-appropriate"
    ),
    "minimal": (
        "a minimal geometric head-and-shoulders avatar, restrained color palette, clean "
        "background, readable at small sizes"
    ),
}


@dataclass(frozen=True)
class AvatarPreview:
    preview_id: str
    agent_id: str
    sha256: str
    created_at: datetime
    style_preset: str
    prompt_note: str | None
    model: str
    url: str

    def payload(self) -> dict[str, Any]:
        return {
            "preview_id": self.preview_id,
            "agent_id": self.agent_id,
            "sha256": self.sha256,
            "created_at": self.created_at.isoformat(),
            "style_preset": self.style_preset,
            "prompt_note": self.prompt_note,
            "model": self.model,
            "mime_type": AVATAR_MIME_TYPE,
            "url": self.url,
        }


class AvatarStorage:
    def __init__(self, runtime_dir: str | Path) -> None:
        root = addon_storage_path(Path(runtime_dir), ADDON_ID, "avatars")
        self.root = root
        self.assets_dir = root / "assets"
        self.previews_dir = root / "previews"

    def normalize_image(self, content: bytes) -> tuple[bytes, str]:
        if len(content) > MAX_AVATAR_UPLOAD_BYTES:
            raise ValueError("avatar image exceeds 5 MiB upload limit")
        try:
            from PIL import Image, ImageOps, UnidentifiedImageError
        except ModuleNotFoundError as exc:
            raise RuntimeError("Pillow is required for Team Add-on avatar image handling") from exc

        try:
            with Image.open(BytesIO(content)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("avatar image must be PNG, JPEG, or WebP")
                normalized = ImageOps.fit(
                    image.convert("RGBA"),
                    (AVATAR_IMAGE_SIZE_PX, AVATAR_IMAGE_SIZE_PX),
                    method=Image.Resampling.LANCZOS,
                )
                output = BytesIO()
                normalized.save(output, format="WEBP", quality=86, method=6)
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("avatar image is not a valid PNG, JPEG, or WebP file") from exc
        normalized_bytes = output.getvalue()
        return normalized_bytes, hashlib.sha256(normalized_bytes).hexdigest()

    def store_upload(self, content: bytes) -> AgentAvatar:
        image_bytes, digest = self.normalize_image(content)
        asset_id = _new_avatar_id()
        self._write_binary(self.asset_path(asset_id), image_bytes)
        return AgentAvatar(
            asset_id=asset_id,
            source="uploaded",
            mime_type=AVATAR_MIME_TYPE,
            sha256=digest,
            created_at=utc_now(),
        )

    def store_generated_preview(
        self,
        *,
        agent_id: str,
        content: bytes,
        style_preset: str,
        prompt_note: str | None,
        model: str,
    ) -> AvatarPreview:
        image_bytes, digest = self.normalize_image(content)
        preview_id = _new_avatar_id()
        created_at = utc_now()
        self._write_binary(self.preview_path(preview_id), image_bytes)
        self._write_json(
            self.preview_metadata_path(preview_id),
            {
                "preview_id": preview_id,
                "agent_id": agent_id,
                "sha256": digest,
                "created_at": created_at.isoformat(),
                "style_preset": style_preset,
                "prompt_note": prompt_note,
                "model": model,
                "mime_type": AVATAR_MIME_TYPE,
            },
        )
        return AvatarPreview(
            preview_id=preview_id,
            agent_id=agent_id,
            sha256=digest,
            created_at=created_at,
            style_preset=style_preset,
            prompt_note=prompt_note,
            model=model,
            url=avatar_preview_url(preview_id),
        )

    def accept_preview(self, *, preview_id: str, agent_id: str) -> AgentAvatar:
        avatar = self.avatar_from_preview(preview_id=preview_id, agent_id=agent_id)
        self.promote_preview(preview_id=preview_id)
        return avatar

    def avatar_from_preview(self, *, preview_id: str, agent_id: str) -> AgentAvatar:
        safe_preview_id = validate_avatar_id(preview_id, "preview_id")
        preview_path = self.preview_path(safe_preview_id)
        metadata_path = self.preview_metadata_path(safe_preview_id)
        if not preview_path.is_file() or not metadata_path.is_file():
            raise FileNotFoundError("avatar preview not found")
        metadata = self._read_json(metadata_path)
        if str(metadata.get("agent_id") or "") != agent_id:
            raise ValueError("avatar preview does not belong to this agent")
        return AgentAvatar(
            asset_id=safe_preview_id,
            source="generated",
            mime_type=AVATAR_MIME_TYPE,
            sha256=str(metadata["sha256"]),
            created_at=_parse_created_at(metadata.get("created_at")),
            style_preset=_optional_text(metadata.get("style_preset")),
            prompt_note=_optional_text(metadata.get("prompt_note")),
            model=_optional_text(metadata.get("model")),
            preview_id=safe_preview_id,
        )

    def promote_preview(self, *, preview_id: str) -> None:
        safe_preview_id = validate_avatar_id(preview_id, "preview_id")
        preview_path = self.preview_path(safe_preview_id)
        metadata_path = self.preview_metadata_path(safe_preview_id)
        if not preview_path.is_file() or not metadata_path.is_file():
            raise FileNotFoundError("avatar preview not found")
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        preview_path.replace(self.asset_path(safe_preview_id))
        metadata_path.unlink(missing_ok=True)

    def asset_path(self, asset_id: str) -> Path:
        return self.assets_dir / f"{validate_avatar_id(asset_id, 'asset_id')}.webp"

    def preview_path(self, preview_id: str) -> Path:
        return self.previews_dir / f"{validate_avatar_id(preview_id, 'preview_id')}.webp"

    def preview_metadata_path(self, preview_id: str) -> Path:
        return self.previews_dir / f"{validate_avatar_id(preview_id, 'preview_id')}.json"

    def delete_asset_if_unreferenced(self, *, asset_id: str | None, state: TeamState) -> None:
        if not asset_id or avatar_asset_referenced(state, asset_id):
            return
        self.asset_path(asset_id).unlink(missing_ok=True)

    @staticmethod
    def _write_binary(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("avatar preview metadata is invalid")
        return payload


class OpenAIAvatarGenerator:
    def __init__(
        self,
        *,
        api_key: str | None,
        api_key_source: str,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        model: str = DEFAULT_AVATAR_MODEL,
        timeout_seconds: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_key_source = api_key_source
        self.base_url = base_url.rstrip("/") or DEFAULT_OPENAI_BASE_URL
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def generate(self, *, prompt: str) -> bytes:
        if not self.api_key:
            raise RuntimeError(
                f"Host environment variable {self.api_key_source} is not set."
            )
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/images/generations",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "n": 1,
                        "size": "1024x1024",
                        "quality": DEFAULT_AVATAR_QUALITY,
                        "output_format": "webp",
                        "output_compression": 85,
                    },
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"OpenAI image generation request failed: {exc}") from exc
        if response.status_code >= 400:
            raise RuntimeError(_openai_error_message(response))
        payload = response.json()
        try:
            encoded = payload["data"][0]["b64_json"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("OpenAI image generation response did not include image data") from exc
        try:
            return base64.b64decode(encoded)
        except ValueError as exc:
            raise RuntimeError("OpenAI image generation returned invalid base64 image data") from exc


def openai_avatar_generator_from_config(config: Any) -> OpenAIAvatarGenerator:
    key_env = str(
        getattr(config, "openai_api_key_env", DEFAULT_OPENAI_API_KEY_ENV)
        or DEFAULT_OPENAI_API_KEY_ENV
    ).strip()
    base_url = str(
        getattr(config, "openai_api_base_url", DEFAULT_OPENAI_BASE_URL)
        or DEFAULT_OPENAI_BASE_URL
    ).strip()
    timeout = float(getattr(config, "openai_relay_timeout_seconds", 120.0) or 120.0)
    return OpenAIAvatarGenerator(
        api_key=os.environ.get(key_env),
        api_key_source=key_env,
        base_url=base_url,
        timeout_seconds=timeout,
    )


def build_avatar_prompt(
    *,
    agent: PersistentAgent,
    role_name: str | None,
    team_name: str | None,
    style_preset: str,
    prompt_note: str | None,
) -> str:
    style_text = STYLE_PRESETS.get(style_preset)
    if style_text is None:
        raise ValueError(f"unknown avatar style preset: {style_preset}")
    display_name = agent.display_name or agent.id
    lines = [
        "Create a square avatar for a software team agent.",
        f"Agent display name: {display_name}.",
        f"Role: {role_name or agent.role_id}.",
    ]
    if team_name:
        lines.append(f"Team: {team_name}.")
    lines.extend(
        [
            f"Visual style: {style_text}.",
            "No text, no letters, no logos, no watermarks.",
            "Keep the face or symbolic subject centered and readable at 48px.",
        ]
    )
    note = _optional_text(prompt_note)
    if note:
        lines.append(f"Operator notes: {note[:500]}.")
    return " ".join(lines)


def normalize_style_preset(value: object) -> str:
    preset = _optional_text(value) or DEFAULT_AVATAR_STYLE
    if preset not in STYLE_PRESETS:
        raise ValueError(f"unknown avatar style preset: {preset}")
    return preset


def normalize_prompt_note(value: object) -> str | None:
    note = _optional_text(value)
    if note is None:
        return None
    return note[:500]


def avatar_asset_url(asset_id: str) -> str:
    return f"/api/addons/team-addon/avatars/{validate_avatar_id(asset_id, 'asset_id')}"


def avatar_preview_url(preview_id: str) -> str:
    return (
        "/api/addons/team-addon/avatar-previews/"
        f"{validate_avatar_id(preview_id, 'preview_id')}"
    )


def avatar_payload(avatar: AgentAvatar | None) -> dict[str, Any] | None:
    if avatar is None:
        return None
    payload = avatar.model_dump(mode="json")
    payload["url"] = avatar_asset_url(avatar.asset_id)
    return payload


def avatar_asset_referenced(state: TeamState, asset_id: str) -> bool:
    normalized = validate_avatar_id(asset_id, "asset_id")
    return any(agent.avatar is not None and agent.avatar.asset_id == normalized for agent in state.agents.values())


def validate_avatar_id(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(text):
        raise ValueError(f"{field_name} is invalid")
    return text


def _new_avatar_id() -> str:
    return f"av_{uuid4().hex}"


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_created_at(value: object) -> datetime:
    text = _optional_text(value)
    if text is None:
        return utc_now()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return utc_now()


def _openai_error_message(response: httpx.Response) -> str:
    request_id = response.headers.get("x-request-id")
    suffix = f" (request id {request_id})" if request_id else ""
    try:
        payload = response.json()
    except ValueError:
        return f"OpenAI image generation failed with HTTP {response.status_code}{suffix}"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = _optional_text(error.get("message")) or "OpenAI image generation failed"
        code = _optional_text(error.get("code"))
        return f"{message}{f' [{code}]' if code else ''}{suffix}"
    return f"OpenAI image generation failed with HTTP {response.status_code}{suffix}"
