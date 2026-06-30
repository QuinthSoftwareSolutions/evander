from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.repositories.skills_library import (
    SkillLibraryEntry,
    SkillLibraryMetadata,
    SkillLibraryRepository,
    normalize_skill_names,
)


class SkillAdminService:
    """Admin service for managed Codex skill-library operations."""

    def __init__(self, skill_library: SkillLibraryRepository) -> None:
        self._skill_library = skill_library

    def list_skills(self) -> list[SkillLibraryMetadata]:
        return self._skill_library.list_skills()

    def create_skill(self, payload: Mapping[str, Any]) -> SkillLibraryMetadata:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("Skill name is required.")
        if "content" not in payload:
            raise ValueError("Skill content is required.")
        return self._skill_library.create_skill(
            name=name,
            content=payload.get("content"),
            replace=bool(payload.get("replace", False)),
        )

    def get_skill(self, name: str) -> SkillLibraryEntry:
        return self._skill_library.get_skill(name)

    def delete_skill(self, name: str) -> SkillLibraryMetadata:
        return self._skill_library.delete_skill(name)

    def validate_managed_codex_skills(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[str]:
        names = normalize_skill_names(skill_names)
        for name in names:
            if not self._skill_library.has_skill(name):
                raise ValueError(f"Unknown Codex skill: {name}")
        return names

    def resolve_container_codex_skill_files(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[dict[str, Any]]:
        names = self.validate_managed_codex_skills(skill_names)
        return self._skill_library.container_file_specs(names)
