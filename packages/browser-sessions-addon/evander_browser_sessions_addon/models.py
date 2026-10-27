"""Strict, bounded contracts shared by hosted browsers and local Chrome hosts."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit
import re

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    field_validator,
    model_validator,
)

ADDON_ID = "browser-sessions-addon"
PROTOCOL_VERSION = 1
TERMINAL = frozenset({"closed", "expired"})
Mode = Literal["hosted", "hosted_demo", "hosted_development", "user_hosted"]
State = Literal[
    "starting",
    "ready",
    "paused",
    "disconnected",
    "closing",
    "closed",
    "expired",
    "failed",
    "cleanup_failed",
]
IDENTIFIER = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
CREDENTIAL_REF = r"^cred_[0-9a-f]{32}$"
ObservationRole = Literal[
    "button",
    "link",
    "textbox",
    "combobox",
    "checkbox",
    "radio",
    "tab",
    "menuitem",
    "option",
    "heading",
    "dialog",
    "alert",
    "gridcell",
    "row",
]


class BrowserError(ValueError):
    def __init__(self, message: str, code: str = "invalid_request", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise BrowserError("Invalid application address") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise BrowserError("Application addresses must be HTTP(S) without credentials")
    host = parsed.hostname.encode("idna").decode().lower()
    host = f"[{host}]" if ":" in host else host
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        host += f":{port}"
    return f"{parsed.scheme}://{host}"


def identifier(value: str) -> str:
    if not re.fullmatch(IDENTIFIER, value):
        raise BrowserError("Invalid identifier")
    return value


def relative_path(value: str) -> str:
    from pathlib import PurePosixPath

    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        raise BrowserError("Expected a repository-relative path")
    return str(path)


class WorkerConnection(Contract):
    base_url: str
    credential_ref: str | None = Field(default=None, pattern=CREDENTIAL_REF)
    parent_worker_url: str
    relay_url: str | None = None

    @field_validator("relay_url")
    @classmethod
    def endpoint_url(cls, value):
        if value is None:
            return value
        parsed = urlsplit(value)
        origin(value.replace("wss://", "https://", 1).replace("ws://", "http://", 1))
        if parsed.path != "/_browser/endpoint" or parsed.query or parsed.fragment:
            raise ValueError("Expected the hosted browser endpoint URL")
        if parsed.scheme in {"http", "ws"} and parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("Public browser endpoints require TLS")
        return value

    @field_validator("base_url", "parent_worker_url")
    @classmethod
    def url(cls, value):
        origin(value)
        if urlsplit(value).query or urlsplit(value).fragment:
            raise ValueError("Worker URLs cannot contain queries or fragments")
        return value.rstrip("/")


class CredentialRequest(Contract):
    expected_revision: int = Field(ge=0, strict=True)
    purpose: Literal["worker", "relay", "voice"]
    worker_id: str | None = Field(default=None, pattern=IDENTIFIER)
    token: SecretStr


class LaunchProfile(Contract):
    """Browser-only authority; application images and launch commands are separate."""

    image: str = Field(default="evander-browser-controller:latest", min_length=1, max_length=256)
    cpus: float = Field(default=1, gt=0, le=8)
    memory_mib: int = Field(default=1536, ge=512, le=8192)
    readonly_mounts: dict[str, str] = Field(default_factory=dict)
    playwright_enabled: bool = False

    @field_validator("image")
    @classmethod
    def image_ref(cls, value):
        if value.startswith("-") or any(c.isspace() for c in value):
            raise ValueError("Invalid approved browser image")
        return value


class VoiceSettings(Contract):
    enabled: bool = False
    api_base_url: str = "https://api.openai.com/v1"
    credential_ref: str | None = Field(default=None, pattern=CREDENTIAL_REF)
    _directory: str = PrivateAttr(default="")
    transcription_model: str = Field(default="gpt-transcribe", min_length=1, max_length=128)
    speech_model: str = Field(default="gpt-4o-mini-tts", min_length=1, max_length=128)
    voice: str = Field(default="onyx", min_length=1, max_length=128)
    speed: float = Field(default=1.1, ge=0.25, le=4.0, allow_inf_nan=False)
    instructions: str = Field(
        default=(
            "Speak in a warm, engaged, conversational tone with natural intonation and clear "
            "articulation. Keep pauses brief and the delivery lightly upbeat."
        ),
        max_length=2000,
    )
    max_recording_seconds: int = Field(default=45, ge=5, le=120)
    max_audio_bytes: int = Field(default=2_000_000, ge=65536, le=5_000_000)
    max_speech_characters: int = Field(default=2000, ge=100, le=4096)
    requests_per_minute: int = Field(default=20, ge=2, le=60)

    @model_validator(mode="before")
    @classmethod
    def compatible_delivery_defaults(cls, value):
        if not isinstance(value, dict):
            return value
        endpoint = value.get("api_base_url", "https://api.openai.com/v1")
        model = value.get("speech_model", "gpt-4o-mini-tts")
        if (
            isinstance(endpoint, str)
            and endpoint.strip().rstrip("/") == "https://api.openai.com/v1"
            and isinstance(model, str)
            and model.strip() in {"gpt-4o-mini-tts", "gpt-4o-mini-tts-2025-12-15"}
        ):
            return value
        # An old custom connection did not opt into optional synthesis controls.
        value = dict(value)
        value.setdefault("speed", 1.0)
        value.setdefault("instructions", "")
        return value

    @field_validator("api_base_url")
    @classmethod
    def endpoint(cls, value):
        origin(value)
        parsed = urlsplit(value)
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Speech providers require HTTPS outside loopback")
        if parsed.query or parsed.fragment:
            raise ValueError("Speech provider URLs cannot contain query parameters or fragments")
        return value.rstrip("/")


class FastExecutionSettings(Contract):
    enabled: bool = False
    max_steps: int = Field(default=20, ge=1, le=40)
    timeout_seconds: float = Field(default=30, ge=1, le=60, allow_inf_nan=False)
    min_probability: dict[Literal["jev", "openai_decisions"], float] = Field(
        default_factory=lambda: {"jev": 0.8, "openai_decisions": 0.8}
    )

    @field_validator("min_probability")
    @classmethod
    def probabilities(cls, values):
        import math

        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in values.values()):
            raise ValueError("Probabilities must be between zero and one")
        return values


class RunValue(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    description: str = Field(
        min_length=1,
        max_length=400,
        description="Explain which field or purpose this exact input serves, so the decision model can select it.",
    )
    value: str = Field(
        max_length=16384,
        description="Literal text to enter, preserving whitespace. Supply an empty string to clear a field.",
    )
    sensitive: bool = Field(
        default=False,
        description="Mask this literal from the decision provider and returned observations. Never put secrets in the goal.",
    )


class CheckpointTarget(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    ref: str | None = Field(default=None, min_length=1, max_length=128)
    role: ObservationRole | None = None
    name: str | None = Field(default=None, max_length=400)
    context: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Exact observed surrounding row, form or dialog name, if needed.",
    )

    @model_validator(mode="after")
    def identified(self):
        if self.ref is None and self.role is None:
            raise ValueError("A checkpoint target needs an observed ref or role")
        return self


class ElementCondition(Contract):
    kind: Literal["present", "absent"]
    target: CheckpointTarget


class ValueCondition(Contract):
    kind: Literal["value"]
    target: CheckpointTarget
    input: str = Field(pattern=IDENTIFIER, description="Compare with this request.values literal.")


class StateCondition(Contract):
    kind: Literal["checked", "expanded", "disabled"]
    target: CheckpointTarget
    expected: bool = Field(strict=True)


class TextCondition(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    kind: Literal["text"]
    text: str = Field(
        min_length=1,
        max_length=1000,
        description="Observed page text that must appear. Only positive text checks are supported.",
    )


class CountCondition(Contract):
    kind: Literal["count"]
    target: CheckpointTarget
    minimum: int = Field(default=0, ge=0, le=600, strict=True)
    maximum: int | None = Field(default=None, ge=0, le=600, strict=True)

    @model_validator(mode="after")
    def bounds(self):
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("Checkpoint maximum must not be below minimum")
        return self


class CheckpointTrigger(Contract):
    action: Literal["click", "fill", "type", "key"]
    ref: str | None = Field(default=None, min_length=1, max_length=128)
    input: str | None = Field(default=None, pattern=IDENTIFIER)
    key: str | None = Field(default=None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def constrained(self):
        if self.action in {"fill", "type"}:
            if self.input is None:
                raise ValueError("A fill/type checkpoint trigger requires a supplied input ID")
        elif self.ref is None or self.input is not None:
            raise ValueError("A click/key checkpoint trigger requires a ref and no input ID")
        if (self.action == "key") != (self.key is not None):
            raise ValueError("Only a key trigger requires a key")
        return self


class RunCheckpoint(Contract):
    id: str = Field(pattern=IDENTIFIER)
    after: CheckpointTrigger
    condition: Annotated[
        ElementCondition | ValueCondition | StateCondition | TextCondition | CountCondition,
        Field(discriminator="kind"),
    ]
    timeout_seconds: float = Field(default=5, ge=0.1, le=15, allow_inf_nan=False)
    stop_after: bool = Field(
        default=False,
        strict=True,
        description="Only on the final checkpoint: return for owning-agent verification as soon as it holds, without another model decision. This is not a claim that the whole goal succeeded.",
    )


class RunRequest(Contract):
    request_id: str = Field(
        pattern=IDENTIFIER,
        description="Fresh identity for this objective. Reuse only with identical arguments to read its existing receipt, never to replay it.",
    )
    generation: int = Field(
        ge=0, description="Current control_generation from this browser session's status."
    )
    goal: str = Field(
        min_length=1,
        max_length=4000,
        description="Short, concrete UI objective using observed control names, explicit ordering dependencies and the expected result. Resolve domain choices and author all text before calling; put every text literal in values. Completion requires your verification.",
    )
    values: dict[str, RunValue] = Field(
        default_factory=dict,
        max_length=200,
        description="Named exact inputs for every fill/type action. Text in the goal alone is not available for entry. Include text you authored beforehand and an empty-string input if a field must be cleared. Omit only for objectives needing no text entry.",
        examples=[
            {
                "filter": {"description": "Search filter text", "value": "Example"},
                "clear": {"description": "Clear the search field after checking it", "value": ""},
            }
        ],
    )
    max_steps: int | None = Field(
        default=None,
        ge=1,
        le=40,
        description="Optional lower step budget, bounded by the directory cap (default 20).",
    )
    timeout_seconds: float | None = Field(
        default=None,
        ge=1,
        le=60,
        allow_inf_nan=False,
        description="Optional lower time budget in seconds, bounded by the directory cap (default 30, maximum 60).",
    )
    checkpoints: list[RunCheckpoint] = Field(
        default_factory=list,
        max_length=8,
        description="Ordered checks triggered after specified actions. Each blocks further input until its observation predicate holds, with metadata-only evidence in the receipt. Counts include every matching observed element, including header rows. Truncated observations cannot prove a checkpoint.",
    )

    @model_validator(mode="after")
    def checkpoint_inputs(self):
        ids = [checkpoint.id for checkpoint in self.checkpoints]
        if len(ids) != len(set(ids)):
            raise ValueError("Checkpoint IDs must be unique")
        if any(checkpoint.stop_after for checkpoint in self.checkpoints[:-1]):
            raise ValueError("Only the final checkpoint can request stop_after")
        for checkpoint in self.checkpoints:
            for input_id in (
                checkpoint.after.input,
                checkpoint.condition.input if checkpoint.condition.kind == "value" else None,
            ):
                if input_id is not None and input_id not in self.values:
                    raise ValueError("Checkpoint input IDs must refer to request.values")
        return self

    @field_validator("values")
    @classmethod
    def value_ids(cls, values):
        if any(not re.fullmatch(IDENTIFIER, key) for key in values):
            raise ValueError("Input value IDs must be identifiers")
        return values


class Settings(Contract):
    enabled: bool = False
    public_base_url: str = ""
    relay_url: str = "http://127.0.0.1:11890"
    relay_credential_ref: str | None = Field(default=None, pattern=CREDENTIAL_REF)
    guest_enabled: bool = True
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    fast_execution: FastExecutionSettings = Field(default_factory=FastExecutionSettings)
    lifetime_seconds: int = Field(default=14400, ge=300, le=86400)
    idle_seconds: int = Field(default=1800, ge=60, le=14400)
    invitation_seconds: int = Field(default=600, ge=60, le=1800)
    retention_days: int = Field(default=30, ge=1, le=365)
    max_hosted_per_worker: int = Field(default=1, ge=1, le=32)
    workers: dict[str, WorkerConnection] = Field(default_factory=dict)
    profiles: dict[str, LaunchProfile] = Field(default_factory=dict)

    @field_validator("public_base_url", "relay_url")
    @classmethod
    def base_url(cls, value):
        if value:
            origin(value)
            if (
                cls.__name__ == "Settings"
                and urlsplit(value).scheme == "http"
                and urlsplit(value).hostname not in {"127.0.0.1", "localhost", "::1"}
                and value != "http://127.0.0.1:11890"
            ):
                raise ValueError("Browser service URLs require HTTPS outside loopback")
            if urlsplit(value).query or urlsplit(value).fragment:
                raise ValueError("Base URLs cannot contain query parameters or fragments")
        return value.rstrip("/")


class Session(Contract):
    id: str = Field(pattern=IDENTIFIER)
    directory: str
    agent_session_id: str
    native_thread_id: str | None = None
    # Retained only to clean up support agents belonging to pre-upgrade sessions.
    support_agent_session_id: str | None = None
    mode: Mode
    target_aliases: list[str] = Field(default_factory=list, max_length=32)
    endpoint_refs: list[str] = Field(default_factory=list, max_length=16)
    entry_url: str | None = None
    endpoint_origins: dict[str, str] = Field(default_factory=dict)
    title: str = Field(default="Shared browser", max_length=160)
    worker_id: str | None = None
    workspace_ref: str | None = None
    launch_manifest_path: str | None = None
    profile_id: str | None = None
    state: State = "starting"
    revision: int = 0
    control_generation: int = 0
    controller: str | None = None
    host_principal: str | None = None
    allowed_origins: list[str] = Field(default_factory=list, max_length=16)
    current_url: str | None = None
    created_at: float
    expires_at: float
    last_activity_at: float
    idle_seconds: int = 1800
    source: dict[str, str] = Field(default_factory=dict)
    error: str | None = None

    @field_validator("allowed_origins")
    @classmethod
    def origins(cls, values):
        return list(dict.fromkeys(origin(value) for value in values))

    @field_validator("launch_manifest_path")
    @classmethod
    def manifest_path(cls, value):
        return relative_path(value) if value else value


class CreateRequest(Contract):
    mode: Literal["hosted", "user_hosted"]
    request_id: str = Field(pattern=IDENTIFIER)
    title: str = Field(default="Shared browser", max_length=160)
    profile_id: str | None = None
    target_aliases: list[str] = Field(default_factory=list, max_length=32)
    endpoint_refs: list[str] = Field(default_factory=list, max_length=16)
    entry_url: str | None = Field(default=None, max_length=8192)

    @model_validator(mode="after")
    def targets(self):
        if self.mode == "hosted":
            if (
                not self.profile_id
                or not self.entry_url
                or not (self.target_aliases or self.endpoint_refs)
            ):
                raise ValueError(
                    "Hosted browsers require a browser profile, approved targets and entry URL"
                )
            origin(self.entry_url)
        elif self.profile_id or self.target_aliases or self.endpoint_refs or self.entry_url:
            raise ValueError("Local Chrome sharing does not accept hosted browser configuration")
        for alias in self.target_aliases:
            identifier(alias)
        import re

        if any(not re.fullmatch(r"ep-[a-f0-9]{32}", ref) for ref in self.endpoint_refs):
            raise ValueError("Invalid resource endpoint reference")
        return self


class BrowserCommand(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    id: str = Field(pattern=IDENTIFIER)
    generation: int = Field(ge=0)
    action: Literal[
        "observe",
        "navigate",
        "click",
        "hover",
        "fill",
        "type",
        "key",
        "scroll",
        "dialog",
        "highlight",
        "reload",
        "pages",
        "select_page",
        "authenticate",
    ]
    target_alias: str | None = Field(default=None, pattern=IDENTIFIER)
    page_id: str | None = Field(default=None, max_length=128)
    observation_id: str | None = Field(default=None, max_length=128)
    ref: str | None = Field(default=None, max_length=128)
    url: str | None = Field(default=None, max_length=8192)
    text: str | None = Field(default=None, max_length=16384)
    x: float | None = Field(default=None, ge=0, le=4096)
    y: float | None = Field(default=None, ge=0, le=4096)
    delta_x: float = Field(default=0, ge=-20000, le=20000)
    delta_y: float = Field(default=0, ge=-20000, le=20000)
    accept: bool = False
    include_image: bool = True
    revalidate: bool = False
    deadline_at: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    def audit(self):
        return {"command_id": self.id, "action": self.action, "ref": self.ref}

    @property
    def observing(self):
        return self.action in {"observe", "highlight", "pages", "select_page"}


class MessageRequest(Contract):
    request_id: str = Field(pattern=IDENTIFIER)
    body: str = Field(min_length=1, max_length=16000)
    annotation: dict[str, str | float] | None = None


class Frame(Contract):
    kind: Literal["frame"] = "frame"
    image: str = Field(max_length=3000000)
    width: int = Field(ge=1, le=4096)
    height: int = Field(ge=1, le=4096)
    page_epoch: str = Field(max_length=128)
    url: str = Field(max_length=8192)
    page_id: str | None = None
    pages: list[dict[str, str | bool]] = Field(default_factory=list, max_length=64)
