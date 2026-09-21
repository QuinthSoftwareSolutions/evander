"""Public, provider-independent inputs for the optional Miro integration."""

from __future__ import annotations

import re
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evander.models import DirectoryPath

ADDON_ID = "miro-addon"
MCP_URL = "https://mcp.miro.com/"
MAX_CONTENT_BYTES = 1_048_576
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:=+-]{0,255}$")]


class MiroError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoardBinding(StrictModel):
    board_id: Identifier
    name: str = Field(default="", max_length=300)
    writable: bool = False


class Connection(StrictModel):
    id: Identifier
    name: str = Field(min_length=1, max_length=200)
    directory_path: str = "root"
    redirect_uri: str
    boards: list[BoardBinding] = Field(default_factory=list, max_length=200)
    allow_workflows: bool = False
    allow_create_boards: bool = False
    daily_call_budget: int = Field(default=100, ge=1, le=100000)
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_session_grants(cls, value: Any) -> Any:
        # Existing connections remain usable after removing the duplicate gate.
        # Session tool policy is enforced by the MCP entry point.
        if isinstance(value, dict) and "session_ids" in value:
            return {key: item for key, item in value.items() if key != "session_ids"}
        return value

    @field_validator("directory_path")
    @classmethod
    def directory(cls, value: str) -> str:
        return DirectoryPath(segments=value).as_posix()

    @field_validator("redirect_uri")
    @classmethod
    def callback(cls, value: str) -> str:
        url = urlsplit(value)
        local = url.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or (url.scheme != "https" and not (local and url.scheme == "http"))
            or url.path != "/api/addons/miro-addon/oauth/callback"
        ):
            raise ValueError(
                "Use the HTTPS Admin Web URL ending /api/addons/miro-addon/oauth/callback"
            )
        return value

    @field_validator("boards")
    @classmethod
    def unique_boards(cls, value: list[BoardBinding]) -> list[BoardBinding]:
        if len({item.board_id for item in value}) != len(value):
            raise ValueError("Board IDs must be unique")
        return value


class OperationInput(StrictModel):
    connection_id: Identifier
    board_id: Identifier | None = None
    frame_id: Identifier | None = None
    item_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    snapshot_id: str | None = None
    operation_key: str | None = Field(default=None, min_length=1, max_length=200)
    operation_id: str | None = None
    svg: str | None = Field(default=None, max_length=MAX_CONTENT_BYTES)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    text: str | None = Field(default=None, min_length=1, max_length=20000)
    comment_id: Identifier | None = None
    resolved: bool = True
    allow_delete: bool = False
    format_name: str | None = Field(default=None, max_length=100)
    source_refs: list[Annotated[str, Field(max_length=2048)]] = Field(
        default_factory=list, max_length=30
    )


READ_OPERATIONS = (
    "list_boards",
    "get_canvas_format",
    "read_canvas",
    "read_comments",
    "get_snapshot",
    "get_operation",
)
WRITE_OPERATIONS = (
    "create_board",
    "create_canvas",
    "update_canvas",
    "reply_comment",
    "resolve_comment",
)
OPERATIONS = READ_OPERATIONS + WRITE_OPERATIONS
UPSTREAM_TOOLS = {
    "get_canvas_format": "canvas_get_canvas_composer_skill",
    "read_canvas": "canvas_read_as_svg",
    "read_comments": "comment_list_comments",
    "create_board": "board_create",
    "create_canvas": "canvas_create_from_svg",
    "update_canvas": "canvas_update_from_svg",
    "reply_comment": "comment_reply",
    "resolve_comment": "comment_resolve",
}


def board_url(board_id: str, frame_id: str | None = None) -> str:
    result = f"https://miro.com/app/board/{quote(board_id, safe='')}/"
    if frame_id:
        result += f"?focusWidget={quote(frame_id, safe='')}"
    return result


def in_directory(owner: str, requested: str) -> bool:
    return requested == owner or requested.startswith(owner + "/")


def require(value: Any, name: str) -> Any:
    if value is None or value == "" or value == []:
        raise MiroError("invalid_input", f"{name} is required")
    return value


def identifier(value: Any) -> str:
    text = str(value)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:=+-]{0,255}", text):
        raise MiroError("invalid_response", "Miro returned an invalid identifier")
    return text
