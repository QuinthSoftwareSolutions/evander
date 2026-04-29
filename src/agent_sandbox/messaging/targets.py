from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_sandbox.services import normalize_teams_target

CommunicationTargetProvider = Literal[
    "teams", "discord", "email", "github", "ado", "jira", "linear"
]
CommunicationTargetKind = Literal["teams", "discord", "email", "pull-request", "ticket"]
CommunicationTargetCapability = Literal["read", "write", "reply", "notify", "comment"]

_ALLOWED_PROVIDER_KINDS: dict[CommunicationTargetProvider, frozenset[CommunicationTargetKind]] = {
    "teams": frozenset({"teams"}),
    "discord": frozenset({"discord"}),
    "email": frozenset({"email"}),
    "github": frozenset({"pull-request", "ticket"}),
    "ado": frozenset({"pull-request", "ticket"}),
    "jira": frozenset({"ticket"}),
    "linear": frozenset({"ticket"}),
}

_SECRET_LIKE_PATTERN = re.compile(
    r"(?i)(token|secret|password|apikey|api_key|xox[baprs]-|gh[pousr]_)"
)
_DISCORD_TARGET_PATTERN = re.compile(r"^(?:(?P<guild>\d{17,20})/)?(?P<channel>\d{17,20})$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CommunicationTargetPolicy(BaseModel):
    capabilities: list[CommunicationTargetCapability] = Field(default_factory=list)
    auth_profile_ref: str | None = None

    @field_validator("auth_profile_ref", mode="before")
    @classmethod
    def normalize_auth_profile_ref(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("capabilities", mode="before")
    @classmethod
    def normalize_capabilities(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        for item in value:
            text = str(item).strip().lower()
            if text:
                normalized.append(text)
        return normalized


class CommunicationTargetDefinition(BaseModel):
    id: str
    provider: CommunicationTargetProvider
    kind: CommunicationTargetKind
    address: str
    policy: CommunicationTargetPolicy = Field(default_factory=CommunicationTargetPolicy)

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> str:
        text = str(value).strip().lower()
        if not text:
            raise ValueError("id must be a non-empty string")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9._:-]{0,126}[a-z0-9])?", text):
            raise ValueError(
                "id must contain only lowercase letters, digits, '.', '_', ':', '-' and start/end with an alphanumeric character"
            )
        return text

    @field_validator("address", mode="before")
    @classmethod
    def normalize_address(cls, value: object) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("address must be a non-empty string")
        if _SECRET_LIKE_PATTERN.search(text):
            raise ValueError(
                "address appears to contain a secret; store credentials via policy.auth_profile_ref instead"
            )
        return text

    @model_validator(mode="after")
    def validate_provider_kind_and_address(self) -> "CommunicationTargetDefinition":
        allowed_kinds = _ALLOWED_PROVIDER_KINDS[self.provider]
        if self.kind not in allowed_kinds:
            allowed = ", ".join(sorted(allowed_kinds))
            raise ValueError(
                f"provider '{self.provider}' is not compatible with kind '{self.kind}' (allowed kinds: {allowed})"
            )

        if self.provider == "teams":
            normalize_teams_target(self.address)
        elif self.provider == "discord":
            if not _DISCORD_TARGET_PATTERN.fullmatch(self.address):
                raise ValueError(
                    "discord address must be '<channel_id>' or '<guild_id>/<channel_id>' using numeric snowflake IDs"
                )
        elif self.provider == "email":
            if not _EMAIL_PATTERN.fullmatch(self.address):
                raise ValueError("email address must be a valid mailbox address")
        return self


class CommunicationTargetsConfig(BaseModel):
    targets: list[CommunicationTargetDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "CommunicationTargetsConfig":
        id_counts = Counter(target.id for target in self.targets)
        duplicates = sorted(target_id for target_id, count in id_counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"communication target IDs must be unique: {', '.join(duplicates)}")
        return self

    def by_id(self) -> dict[str, CommunicationTargetDefinition]:
        return {target.id: target for target in self.targets}


def communication_targets_config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "communication-targets" / "config.json"


def load_communication_targets_config(project_dir: Path) -> CommunicationTargetsConfig:
    path = communication_targets_config_path(project_dir)
    if not path.exists():
        return CommunicationTargetsConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"communication targets config file {path} must contain valid JSON: {exc.msg}"
        ) from exc
    if not isinstance(raw, dict):
        raise ValueError(f"communication targets config file {path} must contain a JSON object")
    try:
        return CommunicationTargetsConfig.model_validate(raw)
    except Exception as exc:
        raise ValueError(f"invalid communication targets config file {path}: {exc}") from exc


def validate_communication_target_refs(
    *, payload: dict[str, object], known_target_ids: set[str]
) -> None:
    if not known_target_ids:
        known_target_ids = set()

    refs: list[tuple[str, str, str]] = []
    for section_name in ("triggers", "gates", "effects", "queries"):
        section = payload.get(section_name)
        if not isinstance(section, list):
            continue
        for entry in section:
            if not isinstance(entry, dict):
                continue
            node_id = str(entry.get("id") or "").strip() or "<unknown-id>"
            _collect_refs_from_mapping(entry, refs, context=f"{section_name}:{node_id}")

    unknown = sorted({ref for (_context, _path, ref) in refs if ref not in known_target_ids})
    if unknown:
        raise ValueError(
            "unknown communication target reference(s): "
            + ", ".join(unknown)
            + "; define them in config/communication-targets/config.json"
        )


def _collect_refs_from_mapping(
    value: object, refs: list[tuple[str, str, str]], *, context: str, path: str = ""
) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            next_path = f"{path}.{key_text}" if path else key_text
            if key_text == "communication_target_id":
                ref = str(nested or "").strip().lower()
                if ref:
                    refs.append((context, next_path, ref))
            elif key_text == "communication_target_ids" and isinstance(nested, list):
                for index, item in enumerate(nested):
                    ref = str(item or "").strip().lower()
                    if ref:
                        refs.append((context, f"{next_path}[{index}]", ref))
            _collect_refs_from_mapping(nested, refs, context=context, path=next_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_refs_from_mapping(item, refs, context=context, path=f"{path}[{index}]")
