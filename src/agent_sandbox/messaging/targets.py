from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.directory_config import (
    DirectoryConfigLayer,
    DirectoryConfigResolution,
    DirectoryConfigValueSource,
)
from agent_sandbox.models import DirectoryInfo
from agent_sandbox.services import normalize_teams_target, parse_teams_target
from agent_sandbox.triggers.providers.schemas import CommunicationNewMessageTriggerDefinition

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
COMMUNICATION_TARGETS_DOMAIN = "communication_targets"
COMMUNICATION_TARGETS_DB_FILENAME = "communication-targets.db"
SQLITE_BUSY_TIMEOUT_MS = 30_000


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


@dataclass(frozen=True, slots=True)
class CommunicationTargetResolution:
    target: CommunicationTargetDefinition
    address: str


class CommunicationTargetOverlayDefinition(BaseModel):
    id: str
    disabled: bool = False
    provider: CommunicationTargetProvider | None = None
    kind: CommunicationTargetKind | None = None
    address: str | None = None
    policy: CommunicationTargetPolicy = Field(default_factory=CommunicationTargetPolicy)

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> str:
        return CommunicationTargetDefinition.normalize_id(value)

    def target_definition(self) -> CommunicationTargetDefinition:
        if self.disabled:
            raise ValueError(f"communication target {self.id!r} is disabled")
        payload = self.model_dump(mode="json", exclude={"disabled"}, exclude_none=True)
        return CommunicationTargetDefinition.model_validate(payload)

    @model_validator(mode="after")
    def validate_active_target(self) -> "CommunicationTargetOverlayDefinition":
        if self.disabled:
            return self
        self.target_definition()
        return self


class CommunicationTargetsOverlayConfig(BaseModel):
    targets: list[CommunicationTargetOverlayDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "CommunicationTargetsOverlayConfig":
        id_counts = Counter(target.id for target in self.targets)
        duplicates = sorted(target_id for target_id, count in id_counts.items() if count > 1)
        if duplicates:
            raise ValueError(
                f"communication target overlay IDs must be unique: {', '.join(duplicates)}"
            )
        return self

    def by_id(self) -> dict[str, CommunicationTargetOverlayDefinition]:
        return {target.id: target for target in self.targets}

    def as_config_payload(self) -> dict[str, Any]:
        targets: list[dict[str, Any]] = []
        for target in self.targets:
            if target.disabled:
                targets.append({"id": target.id, "disabled": True})
            else:
                targets.append(target.target_definition().model_dump(mode="json"))
        return {"targets": targets}


def communication_targets_db_path(runtime_dir: Path) -> Path:
    return runtime_dir / COMMUNICATION_TARGETS_DB_FILENAME


class SqliteCommunicationTargetsRepository:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS communication_target_overlays (
                    directory_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    target_json TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (directory_id, target_id)
                );

                CREATE INDEX IF NOT EXISTS idx_communication_target_overlays_directory
                    ON communication_target_overlays(directory_id, target_id);
                """
            )

    def has_overlay(self, directory_id: str) -> bool:
        self.ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM communication_target_overlays
                WHERE directory_id = ?
                LIMIT 1
                """,
                (directory_id,),
            ).fetchone()
        return row is not None

    def read_overlay(self, directory_id: str) -> CommunicationTargetsOverlayConfig | None:
        self.ensure_schema()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT target_id, disabled, target_json
                FROM communication_target_overlays
                WHERE directory_id = ?
                ORDER BY target_id
                """,
                (directory_id,),
            ).fetchall()
        if not rows:
            return None
        targets: list[dict[str, Any]] = []
        for row in rows:
            target_id = str(row["target_id"])
            disabled = bool(row["disabled"])
            if disabled:
                targets.append({"id": target_id, "disabled": True})
                continue
            payload = _json_loads_object(row["target_json"])
            payload["id"] = target_id
            targets.append(payload)
        return CommunicationTargetsOverlayConfig.model_validate({"targets": targets})

    def write_overlay(
        self, directory_id: str, payload: dict[str, Any]
    ) -> CommunicationTargetsOverlayConfig:
        overlay = CommunicationTargetsOverlayConfig.model_validate(payload)
        self.ensure_schema()
        now = _datetime_to_storage(datetime.now(UTC))
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM communication_target_overlays WHERE directory_id = ?",
                (directory_id,),
            )
            for target in overlay.targets:
                target_json = None
                if not target.disabled:
                    target_json = _json_dumps(target.target_definition().model_dump(mode="json"))
                connection.execute(
                    """
                    INSERT INTO communication_target_overlays(
                        directory_id,
                        target_id,
                        disabled,
                        target_json,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (directory_id, target.id, int(target.disabled), target_json, now),
                )
        return overlay

    def delete_directory_tree(self, directory_ids: set[str]) -> None:
        if not directory_ids:
            return
        self.ensure_schema()
        placeholders = ",".join("?" for _ in directory_ids)
        with self._connect() as connection:
            connection.execute(
                f"DELETE FROM communication_target_overlays WHERE directory_id IN ({placeholders})",
                tuple(sorted(directory_ids)),
            )

    def list_all_target_ids(self) -> set[str]:
        self.ensure_schema()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT target_id
                FROM communication_target_overlays
                WHERE disabled = 0
                ORDER BY target_id
                """
            ).fetchall()
        return {str(row["target_id"]) for row in rows}

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection


class CommunicationTargetsDirectoryResolver:
    def __init__(
        self,
        directory_store: DirectoryStore,
        *,
        repository: SqliteCommunicationTargetsRepository,
    ) -> None:
        self._service = DirectoryService(directory_store)
        self._repository = repository

    def read_overlay(self, reference: str) -> tuple[DirectoryInfo, dict[str, Any] | None]:
        directory = self._service.show(reference).directory
        overlay = self._repository.read_overlay(directory.id)
        return directory, overlay.as_config_payload() if overlay is not None else None

    def write_overlay(self, reference: str, payload: dict[str, Any]) -> tuple[DirectoryInfo, dict[str, Any]]:
        directory = self._service.show(reference).directory
        overlay = self._repository.write_overlay(directory.id, payload)
        return directory, overlay.as_config_payload()

    def resolve(self, reference: str) -> DirectoryConfigResolution:
        details = self._service.show(reference)
        targets_by_id: dict[str, CommunicationTargetDefinition] = {}
        provenance: dict[str, DirectoryConfigValueSource] = {}
        applied_layers: list[DirectoryConfigLayer] = []

        for directory in details.ancestry:
            overlay = self._repository.read_overlay(directory.id)
            if overlay is None:
                continue
            overlay_payload = overlay.as_config_payload()
            applied_layers.append(
                DirectoryConfigLayer(
                    directory_id=directory.id,
                    directory_path=directory.path,
                    domain=COMMUNICATION_TARGETS_DOMAIN,
                    overlay=overlay_payload,
                )
            )
            source = DirectoryConfigValueSource(
                source_type="directory",
                directory_id=directory.id,
                directory_path=directory.path,
            )
            for target_overlay in overlay.targets:
                provenance[f"targets.{target_overlay.id}"] = source
                if target_overlay.disabled:
                    targets_by_id.pop(target_overlay.id, None)
                    continue
                targets_by_id[target_overlay.id] = target_overlay.target_definition()

        config = CommunicationTargetsConfig(
            targets=[targets_by_id[target_id] for target_id in sorted(targets_by_id)]
        )
        return DirectoryConfigResolution(
            domain=COMMUNICATION_TARGETS_DOMAIN,
            selected_directory=details.directory,
            effective_config=config.model_dump(mode="json"),
            provenance=dict(sorted(provenance.items())),
            applied_layers=applied_layers,
            validation_errors=[],
        )

    def resolve_config(self, reference: str) -> CommunicationTargetsConfig:
        resolution = self.resolve(reference)
        return CommunicationTargetsConfig.model_validate(resolution.effective_config)


def load_communication_targets_config(
    *,
    runtime_dir: Path,
    directory_store: DirectoryStore,
    directory_path: str = "root",
) -> CommunicationTargetsConfig:
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
    resolver = CommunicationTargetsDirectoryResolver(
        directory_store, repository=repository
    )
    return resolver.resolve_config(directory_path)


def resolve_communication_target_ref(
    *,
    runtime_dir: Path,
    directory_store: DirectoryStore,
    directory_path: str,
    reference: str,
    provider: CommunicationTargetProvider | None = None,
    capability: CommunicationTargetCapability | None = None,
) -> CommunicationTargetResolution | None:
    normalized_ref = str(reference or "").strip().lower()
    if not normalized_ref:
        return None
    target = load_communication_targets_config(
        runtime_dir=runtime_dir,
        directory_store=directory_store,
        directory_path=directory_path,
    ).by_id().get(normalized_ref)
    if target is None:
        return None
    if provider is not None and target.provider != provider:
        raise ValueError(
            f"communication target {target.id!r} uses provider {target.provider!r}, "
            f"not {provider!r}"
        )
    if capability is not None and capability not in set(target.policy.capabilities):
        raise ValueError(
            f"communication target {target.id!r} does not allow {capability} capability"
        )
    return CommunicationTargetResolution(target=target, address=target.address)


def known_communication_target_ids(
    *,
    runtime_dir: Path,
    directory_store: DirectoryStore,
) -> set[str]:
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
    return repository.list_all_target_ids()


def validate_communication_new_message_trigger_targets(
    trigger: CommunicationNewMessageTriggerDefinition,
    *,
    runtime_dir: Path,
    directory_store: DirectoryStore,
) -> None:
    targets = load_communication_targets_config(
        runtime_dir=runtime_dir,
        directory_store=directory_store,
        directory_path=trigger.directory_path or "root",
    ).by_id()
    for target_id in trigger.communication_target_ids:
        target = targets.get(target_id)
        if target is None:
            raise ValueError(
                "trigger:communication:new-message target "
                f"{target_id!r} is not defined for directory {trigger.directory_path or 'root'!r}"
            )
        if target.provider not in {"teams", "discord"}:
            raise ValueError(
                "trigger:communication:new-message target "
                f"{target_id!r} uses provider {target.provider!r}; only teams and discord are supported"
            )
        if target.provider == "teams":
            parsed_target = parse_teams_target(target.address)
            if parsed_target.kind == "user":
                raise ValueError(
                    "trigger:communication:new-message target "
                    f"{target_id!r} is a Teams user target; only channel and chat targets are supported"
                )
        if "read" not in set(target.policy.capabilities):
            raise ValueError(
                "trigger:communication:new-message target "
                f"{target_id!r} does not allow read capability"
            )


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
            + "; define them in Admin Web directory configuration under Communications"
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
                if ref and not _is_dynamic_communication_target_ref(ref):
                    refs.append((context, next_path, ref))
            elif key_text == "communication_target_ids" and isinstance(nested, list):
                for index, item in enumerate(nested):
                    ref = str(item or "").strip().lower()
                    if ref and not _is_dynamic_communication_target_ref(ref):
                        refs.append((context, f"{next_path}[{index}]", ref))
            _collect_refs_from_mapping(nested, refs, context=context, path=next_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_refs_from_mapping(item, refs, context=context, path=f"{path}[{index}]")


def _is_dynamic_communication_target_ref(value: str) -> bool:
    return "${{" in value and "}}" in value


def _datetime_to_storage(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_dumps(payload: Any) -> str:
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _json_loads_object(payload: str | bytes | None) -> dict[str, Any]:
    if payload is None:
        return {}
    try:
        import json

        value = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
