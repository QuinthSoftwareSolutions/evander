"""Deterministic, bounded progress graphics. No browser, network or inference."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from evander.models import ToolName
from evander.communication_artifacts import CommunicationArtifactStore


class ProgressSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    generation_id: str = Field(min_length=1, max_length=200)
    outline_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    snapshot_sequence: StrictInt = Field(ge=0)
    document_title: str = Field(min_length=1, max_length=160)
    phase: Literal[
        "writing",
        "reviewing",
        "repairing",
        "exporting",
        "verifying",
        "delivered",
        "blocked",
        "failed",
        "cancelled",
    ]
    chapters_total: StrictInt = Field(ge=0, le=1_000_000_000)
    chapters_completed: StrictInt = Field(ge=0, le=1_000_000_000)
    sections_total: StrictInt = Field(ge=0, le=1_000_000_000)
    sections_completed: StrictInt = Field(ge=0, le=1_000_000_000)
    status: str = Field(max_length=280)

    @model_validator(mode="after")
    def consistent_counts(self):
        if (
            self.chapters_completed > self.chapters_total
            or self.sections_completed > self.sections_total
        ):
            raise ValueError("Completed counts cannot exceed totals")
        if not self.generation_id.strip() or not self.document_title.strip():
            raise ValueError("Generation and title must be nonempty")
        return self

    def visible_hash(self):
        fields = self.model_dump(exclude={"generation_id", "outline_sha256", "snapshot_sequence"})
        return sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def status_text(self):
        return (
            f"{self.document_title} — {self.phase.title()}: "
            f"{self.chapters_completed}/{self.chapters_total} chapters drafted; "
            f"{self.sections_completed}/{self.sections_total} sections drafted. {self.status}"
        ).strip()


def _wrapped(text, font, width):
    """Bound even a single long identifier without depending on platform font fallback."""
    lines, line = [], ""
    for character in text.replace("\n", " "):
        if font.getlength(line + character) > width and line:
            space = line.rfind(" ")
            if space > len(line) // 2:
                lines.append(line[:space])
                line = line[space + 1 :]
            else:
                lines.append(line)
                line = ""
        line += character
    if line:
        lines.append(line)
    return lines


def render_progress_png(snapshot: ProgressSnapshot) -> bytes:
    snapshot = ProgressSnapshot.model_validate(snapshot)
    font_path = str(Path(__file__).with_name("assets") / "fonts" / "DejaVuSans.ttf")
    title = ImageFont.truetype(font_path, 28)
    while len(_wrapped(snapshot.document_title, title, 864)) > 2 and title.size > 16:
        title = ImageFont.truetype(font_path, title.size - 2)
    normal = ImageFont.truetype(font_path, 21)
    small = ImageFont.truetype(font_path, 18)
    image = Image.new("RGB", (960, 480), "#f4f7fc")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((12, 12, 948, 468), radius=20, fill="white", outline="#dce3ef")
    y = 30
    for line in _wrapped(snapshot.document_title, title, 864):
        draw.text((48, y), line, font=title, fill="#12243e")
        y += title.size + 8
    y = max(y + 12, 116)
    draw.text((48, y), snapshot.phase.title(), font=normal, fill="#35577d")
    y += 38
    draw.text(
        (48, y),
        f"Chapters drafted: {snapshot.chapters_completed} / {snapshot.chapters_total}",
        font=normal,
        fill="#12243e",
    )
    y += 40
    draw.rounded_rectangle((48, y, 912, y + 26), radius=12, fill="#e5ebf4")
    pixels = (
        round(864 * snapshot.chapters_completed / snapshot.chapters_total)
        if snapshot.chapters_total
        else 0
    )
    if pixels:
        draw.rounded_rectangle(
            (48, y, 48 + pixels, y + 26), radius=min(12, pixels // 2), fill="#227cbd"
        )
    y += 44
    draw.text(
        (48, y),
        f"Sections drafted: {snapshot.sections_completed} / {snapshot.sections_total}",
        font=normal,
        fill="#12243e",
    )
    y += 40
    for line in _wrapped(snapshot.status, small, 864):
        if y > 441:
            raise ValueError("Progress text exceeds the supported graphic layout")
        draw.text((48, y), line, font=small, fill="#425873")
        y += 24
    output = BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


def stage_progress_image(runtime, session_id, snapshot, *, authorize_session=None):
    session = runtime.policy.require_tool(session_id, ToolName.TEAMS_POST_FILE)
    if authorize_session is not None:
        authorize_session(session)
    snapshot = ProgressSnapshot.model_validate(snapshot)
    content = render_progress_png(snapshot)
    runtime.policy.require_communication_file_send_enabled(session)
    runtime.policy.require_communication_file_content(
        session,
        filename="progress.png",
        mime_type="image/png",
        byte_count=len(content),
    )
    store = CommunicationArtifactStore(runtime.config.runtime_dir)
    artifact = store.stage(
        provider="teams",
        session_id=session_id,
        content=content,
        original_filename="progress.png",
        mime_type="image/png",
        metadata={"status_text": snapshot.status_text(), "purpose": "teams_progress_image"},
    )
    return {
        "artifact_id": artifact.artifact_id,
        "sha256": artifact.sha256,
        "expires_at": artifact.expires_at.isoformat(),
        "status_text": snapshot.status_text(),
    }
