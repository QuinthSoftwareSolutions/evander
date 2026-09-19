"""The bounded, provider-independent surface shared by Figma tools and workflows."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ADDON_ID = "figma-addon"
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
# IDs inside component instances can contain an I prefix and semicolon-separated paths.
NodeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9:;_-]{1,256}$")]
RequestId = Annotated[str, StringConstraints(min_length=1, max_length=160)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Connection(Model):
    client_id: str = Field(min_length=1, max_length=256)
    client_secret_env: Identifier
    redirect_uri: str
    rest_token_env: Identifier | None = None
    rest_token_kind: Literal["personal", "oauth"] = "personal"

    @model_validator(mode="after")
    def validate_redirect(self):
        from urllib.parse import urlsplit

        url = urlsplit(self.redirect_uri)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path != "/api/addons/figma-addon/oauth/callback"
        ):
            raise ValueError("redirect_uri must be an HTTPS Figma add-on OAuth callback")
        return self

    def binding(self) -> str:
        return digest(self.model_dump())


class Grant(Model):
    access: list[Literal["read", "comment", "edit"]] = Field(default_factory=list)
    # Empty means the registered file. Nonempty restricts canvas edits to these subtrees.
    edit_roots: list[NodeId] = Field(default_factory=list, max_length=100)


class Target(Model):
    name: str = Field(min_length=1, max_length=200)
    connection_id: Identifier
    file_key: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9]{6,128}$")]
    grants: dict[Identifier, Grant] = Field(default_factory=dict)


class Settings(Model):
    connections: dict[Identifier, Connection] = Field(default_factory=dict)
    targets: dict[Identifier, Target] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_connections(self):
        for target in self.targets.values():
            if target.connection_id not in self.connections:
                raise ValueError("Target references an unknown connection")
        return self


class Properties(Model):
    name: str | None = Field(default=None, max_length=200)
    x: float | None = Field(default=None, ge=-100000, le=100000)
    y: float | None = Field(default=None, ge=-100000, le=100000)
    width: float | None = Field(default=None, gt=0, le=10000)
    height: float | None = Field(default=None, gt=0, le=10000)
    characters: str | None = Field(default=None, max_length=10000)
    font_size: float | None = Field(default=None, ge=1, le=500)
    font_family: str | None = Field(default=None, max_length=100)
    font_style: str | None = Field(default=None, max_length=100)
    fill: Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None
    corner_radius: float | None = Field(default=None, ge=0, le=5000)
    opacity: float | None = Field(default=None, ge=0, le=1)
    visible: bool | None = None
    layout_mode: Literal["NONE", "HORIZONTAL", "VERTICAL"] | None = None
    item_spacing: float | None = Field(default=None, ge=0, le=1000)
    padding: float | None = Field(default=None, ge=0, le=1000)
    horizontal_sizing: Literal["FIXED", "HUG", "FILL"] | None = None
    vertical_sizing: Literal["FIXED", "HUG", "FILL"] | None = None
    # Existing file/library assets are referenced explicitly; no network URLs or scripts.
    text_style_id: str | None = Field(default=None, max_length=160)
    fill_variable_id: str | None = Field(default=None, max_length=160)


class Edit(Model):
    action: Literal["create", "update", "clone", "move", "remove"]
    node_id: NodeId | None = None
    parent_id: NodeId | None = None
    kind: Literal["FRAME", "TEXT", "RECTANGLE", "ELLIPSE", "INSTANCE"] | None = None
    component_id: NodeId | None = None
    properties: Properties = Field(default_factory=Properties)

    @model_validator(mode="after")
    def validate_shape(self):
        if self.action == "create":
            if not self.parent_id or not self.kind or self.node_id:
                raise ValueError("create requires parent_id and kind, without node_id")
            if (self.kind == "INSTANCE") != bool(self.component_id):
                raise ValueError("INSTANCE creation requires component_id exclusively")
        elif not self.node_id or self.kind or self.component_id:
            raise ValueError("This edit requires node_id, without kind or component_id")
        if self.action in {"move", "clone"} and not self.parent_id:
            raise ValueError("move/clone requires parent_id")
        if self.action in {"update", "remove"} and self.parent_id:
            raise ValueError("update/remove cannot set parent_id")
        if self.action == "remove" and self.properties.model_dump(exclude_none=True):
            raise ValueError("remove cannot set properties")
        return self


class DesignRequest(Model):
    operation: Literal[
        "pages",
        "inspect",
        "variables",
        "comments",
        "history",
        "create_area",
        "edit",
        "compose",
        "comment",
        "snapshot",
        "import_snapshot",
        "reconcile",
    ]
    target_id: Identifier
    node_id: NodeId | None = None
    request_id: RequestId | None = None
    expected_revision: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    edits: list[Edit] = Field(default_factory=list, max_length=50)
    source_snapshot_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    message: str | None = Field(default=None, min_length=1, max_length=10000)
    comment_id: str | None = Field(default=None, max_length=100)
    snapshot_id: Identifier | None = None
    operation_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_operation(self):
        required = {
            "pages": (),
            "inspect": ("node_id",),
            "variables": ("node_id",),
            "comments": (),
            "history": (),
            "create_area": ("name", "request_id"),
            "edit": ("node_id", "request_id", "expected_revision", "edits"),
            "compose": ("node_id", "request_id", "expected_revision", "source_snapshot_ids"),
            "comment": ("message", "request_id"),
            "snapshot": ("node_id", "request_id"),
            "import_snapshot": ("snapshot_id",),
            "reconcile": ("operation_id",),
        }[self.operation]
        for key in required:
            if not getattr(self, key):
                raise ValueError(f"{self.operation} requires {key}")
        allowed = {
            "pages": set(),
            "inspect": {"node_id"},
            "variables": {"node_id"},
            "comments": set(),
            "history": set(),
            "create_area": {"name", "node_id", "request_id", "expected_revision"},
            "edit": set(required),
            "compose": set(required),
            "comment": set(required) | {"node_id", "comment_id"},
            "snapshot": set(required),
            "import_snapshot": set(required),
            "reconcile": set(required),
        }[self.operation] | {"operation", "target_id"}
        if any(
            value not in (None, [])
            for key, value in self.model_dump().items()
            if key not in allowed
        ):
            raise ValueError("Fields do not match the selected operation")
        if self.operation == "create_area" and self.node_id and not self.expected_revision:
            raise ValueError("Creating an area inside a node requires expected_revision")
        return self


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode()
    ).hexdigest()


class FigmaError(ValueError):
    """Safe to present to an operator or agent; never contains an upstream response."""


class FigmaConflict(FigmaError):
    pass


class FigmaUnavailable(FigmaError):
    pass
