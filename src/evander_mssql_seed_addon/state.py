from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evander.addons import addon_json_object_store
from evander.worker.models import SAFE_ID_PATTERN

ADDON_ID = "mssql-seed-addon"
STATE_FILE = "state.json"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class MssqlSeedStateError(RuntimeError):
    pass


class MssqlSeedSidecarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sidecar_id: str
    base_url: str
    token_env: str = "EVANDER_MSSQL_SEED_SIDECAR_TOKEN"
    restore_timeout_seconds: float = 3600.0
    description: str | None = None

    @field_validator("sidecar_id", mode="before")
    @classmethod
    def normalize_sidecar_id(cls, value: object) -> str:
        return _safe_id(value, "sidecar_id")

    @field_validator("base_url", mode="before")
    @classmethod
    def normalize_base_url(cls, value: object) -> str:
        text = str(value or "").strip().rstrip("/")
        if not text:
            raise ValueError("base_url is required")
        if not (text.startswith("http://") or text.startswith("https://")):
            raise ValueError("base_url must start with http:// or https://")
        return text

    @field_validator("token_env", mode="before")
    @classmethod
    def normalize_token_env(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("token_env is required")
        if not SAFE_ID_PATTERN.fullmatch(text):
            raise ValueError("token_env must contain only letters, numbers, dots, underscores, or dashes")
        return text

    @field_validator("restore_timeout_seconds", mode="before")
    @classmethod
    def normalize_restore_timeout_seconds(cls, value: object) -> float:
        if value is None or str(value).strip() == "":
            return 3600.0
        try:
            timeout = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("restore_timeout_seconds must be a number") from exc
        if timeout <= 0:
            raise ValueError("restore_timeout_seconds must be positive")
        return timeout

    @field_validator("description", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class MssqlSeedDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed_alias: str
    sidecar_id: str
    backup_path: str
    description: str | None = None

    @field_validator("seed_alias", mode="before")
    @classmethod
    def normalize_seed_alias(cls, value: object) -> str:
        return _safe_id(value, "seed_alias")

    @field_validator("sidecar_id", mode="before")
    @classmethod
    def normalize_sidecar_id(cls, value: object) -> str:
        return _safe_id(value, "sidecar_id")

    @field_validator("backup_path", mode="before")
    @classmethod
    def normalize_backup_path(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("backup_path is required")
        if "\x00" in text:
            raise ValueError("backup_path must not contain NUL bytes")
        if not Path(text).is_absolute():
            raise ValueError("backup_path must be an absolute path on the worker sidecar host")
        return text

    @field_validator("description", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class MssqlSeedDatabaseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    logical_name: str
    seed_alias: str
    sidecar_id: str
    database_name: str
    username: str
    server: str
    port: int
    restored_at: str
    last_reset_at: str | None = None
    reset_count: int = 0

    @field_validator("session_id", "logical_name", "seed_alias", "sidecar_id", mode="before")
    @classmethod
    def normalize_safe_ids(cls, value: object, info) -> str:
        return _safe_id(value, info.field_name)

    @field_validator("database_name", "username", "server", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} is required")
        return text

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if value <= 0 or value > 65535:
            raise ValueError("port must be between 1 and 65535")
        return value

    @property
    def key(self) -> str:
        return database_key(self.session_id, self.logical_name)


class MssqlSeedState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sidecars: dict[str, MssqlSeedSidecarConfig] = Field(default_factory=dict)
    seeds: dict[str, MssqlSeedDefinition] = Field(default_factory=dict)
    databases: dict[str, MssqlSeedDatabaseRecord] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> "MssqlSeedState":
        for key, sidecar in self.sidecars.items():
            if key != sidecar.sidecar_id:
                raise ValueError(f"sidecar key {key!r} must match sidecar_id")
        for key, seed in self.seeds.items():
            if key != seed.seed_alias:
                raise ValueError(f"seed key {key!r} must match seed_alias")
            if seed.sidecar_id not in self.sidecars:
                raise ValueError(
                    f"seed {seed.seed_alias!r} references unknown sidecar {seed.sidecar_id!r}"
                )
        for key, database in self.databases.items():
            if key != database.key:
                raise ValueError(f"database key {key!r} must match session_id/logical_name")
            if database.seed_alias not in self.seeds:
                raise ValueError(
                    f"database {key!r} references unknown seed {database.seed_alias!r}"
                )
        return self


class MssqlSeedStore:
    def __init__(self, runtime_dir: str | Path) -> None:
        self._store = addon_json_object_store(
            runtime_dir,
            ADDON_ID,
            STATE_FILE,
            initial_object=MssqlSeedState().model_dump(mode="json"),
            file_mode=0o600,
        )

    def read(self) -> MssqlSeedState:
        return MssqlSeedState.model_validate(self._store.read_object())

    def payload(self) -> dict[str, Any]:
        return self.read().model_dump(mode="json")

    def replace_settings(
        self,
        *,
        sidecars: dict[str, Any] | list[Any],
        seeds: dict[str, Any] | list[Any],
    ) -> MssqlSeedState:
        def _update(current: dict[str, Any]) -> dict[str, Any]:
            existing = MssqlSeedState.model_validate(current or {})
            next_sidecars = _sidecars_by_id(sidecars)
            next_seeds = _seeds_by_alias(seeds)
            state = MssqlSeedState(
                sidecars=next_sidecars,
                seeds=next_seeds,
                databases={
                    key: database
                    for key, database in existing.databases.items()
                    if database.seed_alias in next_seeds
                },
            )
            payload = state.model_dump(mode="json")
            current.clear()
            current.update(payload)
            return payload

        return MssqlSeedState.model_validate(self._store.update_object(_update))

    def get_seed(self, seed_alias: str) -> MssqlSeedDefinition:
        normalized = _safe_id(seed_alias, "seed_alias")
        seed = self.read().seeds.get(normalized)
        if seed is None:
            raise MssqlSeedStateError(f"MSSQL seed alias {normalized!r} is not configured")
        return seed

    def get_sidecar(self, sidecar_id: str) -> MssqlSeedSidecarConfig:
        normalized = _safe_id(sidecar_id, "sidecar_id")
        sidecar = self.read().sidecars.get(normalized)
        if sidecar is None:
            raise MssqlSeedStateError(f"MSSQL seed sidecar {normalized!r} is not configured")
        return sidecar

    def upsert_database(self, record: MssqlSeedDatabaseRecord) -> MssqlSeedDatabaseRecord:
        def _update(current: dict[str, Any]) -> dict[str, Any]:
            state = MssqlSeedState.model_validate(current or {})
            state.databases[record.key] = record
            payload = state.model_dump(mode="json")
            current.clear()
            current.update(payload)
            return payload

        updated = MssqlSeedState.model_validate(self._store.update_object(_update))
        return updated.databases[record.key]

    def get_database(self, session_id: str, logical_name: str) -> MssqlSeedDatabaseRecord:
        key = database_key(session_id, logical_name)
        database = self.read().databases.get(key)
        if database is None:
            raise MssqlSeedStateError(
                f"No seeded MSSQL database is tracked for {session_id!r}/{logical_name!r}"
            )
        return database

    def list_databases(self, session_id: str | None = None) -> list[MssqlSeedDatabaseRecord]:
        state = self.read()
        normalized_session_id = (
            _safe_id(session_id, "session_id") if session_id is not None else None
        )
        databases = [
            database
            for database in state.databases.values()
            if normalized_session_id is None or database.session_id == normalized_session_id
        ]
        return sorted(databases, key=lambda item: (item.session_id, item.logical_name))


def database_key(session_id: str, logical_name: str) -> str:
    return f"{_safe_id(session_id, 'session_id')}:{_safe_id(logical_name, 'logical_name')}"


def _safe_id(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if not SAFE_ID_PATTERN.fullmatch(text):
        raise ValueError(
            f"{field_name} must contain only letters, numbers, dots, underscores, or dashes"
        )
    if len(text) > 128:
        raise ValueError(f"{field_name} must be 128 characters or fewer")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _sidecars_by_id(value: dict[str, Any] | list[Any]) -> dict[str, MssqlSeedSidecarConfig]:
    items: list[Any]
    if isinstance(value, dict):
        items = []
        for key, raw in value.items():
            payload = dict(raw) if isinstance(raw, dict) else {"base_url": raw}
            payload.setdefault("sidecar_id", key)
            items.append(payload)
    elif isinstance(value, list):
        items = value
    else:
        raise MssqlSeedStateError("sidecars must be a JSON object or array")
    sidecars: dict[str, MssqlSeedSidecarConfig] = {}
    for raw in items:
        sidecar = MssqlSeedSidecarConfig.model_validate(raw)
        sidecars[sidecar.sidecar_id] = sidecar
    return sidecars


def _seeds_by_alias(value: dict[str, Any] | list[Any]) -> dict[str, MssqlSeedDefinition]:
    items: list[Any]
    if isinstance(value, dict):
        items = []
        for key, raw in value.items():
            payload = dict(raw) if isinstance(raw, dict) else {"backup_path": raw}
            payload.setdefault("seed_alias", key)
            items.append(payload)
    elif isinstance(value, list):
        items = value
    else:
        raise MssqlSeedStateError("seeds must be a JSON object or array")
    seeds: dict[str, MssqlSeedDefinition] = {}
    for raw in items:
        seed = MssqlSeedDefinition.model_validate(raw)
        seeds[seed.seed_alias] = seed
    return seeds
