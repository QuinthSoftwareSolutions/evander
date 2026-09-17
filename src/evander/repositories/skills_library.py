from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

from evander.storage import JsonStore

_SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_CONTAINER_CODEX_SKILLS_ROOT = "/home/agent/.config/evander/codex/skills"
CODEX_SKILL_INSTALLATIONS_METADATA_KEY = "codex_skill_installations"


class SkillLibraryMetadata(BaseModel):
    name: str
    size_bytes: int
    updated_at: datetime

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> str:
        return validate_skill_name(value)


class SkillLibraryEntry(SkillLibraryMetadata):
    content: str


def validate_skill_name(value: object) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError("Skill name is required.")
    if name in {".", ".."}:
        raise ValueError("Skill name must not be '.' or '..'.")
    if not _SKILL_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            "Skill name must contain only letters, numbers, dots, underscores, or dashes."
        )
    return name


def normalize_skill_names(skill_names: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_name in skill_names or []:
        name = validate_skill_name(raw_name)
        if name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return normalized


def record_codex_skill_installations(
    metadata: Mapping[str, Any] | None,
    skill_names: list[str] | tuple[str, ...] | None,
    *,
    backend: str,
    vm_user: str | None = None,
) -> dict[str, Any]:
    names = normalize_skill_names(skill_names)
    normalized_backend = str(backend or "").strip()
    if not normalized_backend:
        raise ValueError("Codex skill installation backend is required.")
    normalized_vm_user = str(vm_user or "").strip()
    updated = dict(metadata or {})
    existing_names = updated.get("codex_skills")
    merged_names = (
        [str(item).strip() for item in existing_names if isinstance(item, str) and item.strip()]
        if isinstance(existing_names, list)
        else []
    )
    merged_names.extend(names)
    updated["codex_skills"] = normalize_skill_names(merged_names)

    existing_installations = updated.get(CODEX_SKILL_INSTALLATIONS_METADATA_KEY)
    installations = (
        {
            str(name): dict(target)
            for name, target in existing_installations.items()
            if isinstance(name, str) and name.strip() and isinstance(target, Mapping)
        }
        if isinstance(existing_installations, Mapping)
        else {}
    )
    for name in names:
        target: dict[str, str] = {"backend": normalized_backend}
        if normalized_backend == "libvirt_vm":
            target["vm_user"] = normalized_vm_user or "agent"
        installations[name] = target
    updated[CODEX_SKILL_INSTALLATIONS_METADATA_KEY] = installations
    updated["codex_skills_updated_at"] = datetime.now(UTC).isoformat()
    return updated


def codex_skill_install_vm_user(
    metadata: Mapping[str, Any] | None,
    skill_name: str,
) -> str:
    normalized_name = validate_skill_name(skill_name)
    if isinstance(metadata, Mapping):
        installations = metadata.get(CODEX_SKILL_INSTALLATIONS_METADATA_KEY)
        if isinstance(installations, Mapping):
            target = installations.get(normalized_name)
            if isinstance(target, Mapping):
                vm_user = str(target.get("vm_user") or "").strip()
                if vm_user:
                    return vm_user
        legacy_vm_user = str(metadata.get("vm_user") or "").strip()
        if legacy_vm_user:
            return legacy_vm_user
    return "agent"


class SkillLibraryRepository:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.metadata_path = runtime_dir / "skills-library.json"
        self.skills_root = runtime_dir / "skills"
        self._store = JsonStore(self.metadata_path, SkillLibraryMetadata)
        self.skills_root.mkdir(parents=True, exist_ok=True)

    def list_skills(self) -> list[SkillLibraryMetadata]:
        return sorted(self._store.read_all().values(), key=lambda item: item.name.casefold())

    def has_skill(self, name: object) -> bool:
        return self._store.get(validate_skill_name(name)) is not None

    def get_skill(self, name: object) -> SkillLibraryEntry:
        normalized_name = validate_skill_name(name)
        metadata = self._store.get(normalized_name)
        if metadata is None:
            raise LookupError(f"Unknown skill: {normalized_name}")
        skill_file = self._skill_file(normalized_name)
        try:
            content = skill_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"Managed skill content is missing: {normalized_name}") from exc
        return SkillLibraryEntry(
            name=metadata.name,
            size_bytes=metadata.size_bytes,
            updated_at=metadata.updated_at,
            content=content,
        )

    def create_skill(
        self,
        *,
        name: object,
        content: object,
        replace: bool = False,
    ) -> SkillLibraryMetadata:
        normalized_name = validate_skill_name(name)
        if not isinstance(content, str):
            raise ValueError("Skill content must be a string.")
        if not content.strip():
            raise ValueError("Skill content must not be empty.")
        existing = self._store.get(normalized_name)
        if existing is not None and not replace:
            raise FileExistsError(f"Skill already exists: {normalized_name}")

        encoded = content.encode("utf-8")
        skill_dir = self._skill_dir(normalized_name)
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = self._skill_file(normalized_name)
        temp_path = skill_file.with_name(f".{skill_file.name}.tmp")
        try:
            with temp_path.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, skill_file)
        finally:
            if temp_path.exists():
                temp_path.unlink()

        metadata = SkillLibraryMetadata(
            name=normalized_name,
            size_bytes=len(encoded),
            updated_at=datetime.now(UTC),
        )
        self._store.put(normalized_name, metadata)
        return metadata

    def delete_skill(self, name: object) -> SkillLibraryMetadata:
        normalized_name = validate_skill_name(name)
        metadata = self._store.get(normalized_name)
        if metadata is None:
            raise LookupError(f"Unknown skill: {normalized_name}")
        skill_file = self._skill_file(normalized_name)
        if skill_file.exists():
            skill_file.unlink()
        skill_dir = self._skill_dir(normalized_name)
        try:
            skill_dir.rmdir()
        except OSError:
            pass
        self._store.delete(normalized_name)
        return metadata

    def container_file_specs(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for name in normalize_skill_names(skill_names):
            entry = self.get_skill(name)
            specs.append(
                {
                    "destination_path": f"{_CONTAINER_CODEX_SKILLS_ROOT}/{entry.name}/SKILL.md",
                    "mode": "0644",
                    "content": entry.content,
                }
            )
        return specs

    def _skill_dir(self, name: str) -> Path:
        return self.skills_root / name

    def _skill_file(self, name: str) -> Path:
        return self._skill_dir(name) / "SKILL.md"


__all__ = [
    "CODEX_SKILL_INSTALLATIONS_METADATA_KEY",
    "SkillLibraryEntry",
    "SkillLibraryMetadata",
    "SkillLibraryRepository",
    "codex_skill_install_vm_user",
    "normalize_skill_names",
    "record_codex_skill_installations",
    "validate_skill_name",
]
