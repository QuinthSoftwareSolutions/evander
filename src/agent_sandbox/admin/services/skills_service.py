from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from agent_sandbox.repositories.skills_library import SkillLibraryEntry, SkillLibraryMetadata


class _SkillsAdminServiceHost(Protocol):
    def _list_skills_impl(self) -> list["SkillLibraryMetadata"]: ...
    def _create_skill_impl(self, payload: Mapping[str, Any]) -> "SkillLibraryMetadata": ...
    def _get_skill_impl(self, name: str) -> "SkillLibraryEntry": ...
    def _delete_skill_impl(self, name: str) -> "SkillLibraryMetadata": ...
    def _validate_managed_codex_skills_impl(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[str]: ...
    def _resolve_container_codex_skill_files_impl(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[dict[str, Any]]: ...


class SkillsAdminService:
    """Admin skill-library operations extracted from the public AdminService facade."""

    def __init__(self, host: _SkillsAdminServiceHost) -> None:
        self._host = host

    def list_skills(self) -> list["SkillLibraryMetadata"]:
        return self._host._list_skills_impl()

    def create_skill(self, payload: Mapping[str, Any]) -> "SkillLibraryMetadata":
        return self._host._create_skill_impl(payload)

    def get_skill(self, name: str) -> "SkillLibraryEntry":
        return self._host._get_skill_impl(name)

    def delete_skill(self, name: str) -> "SkillLibraryMetadata":
        return self._host._delete_skill_impl(name)

    def validate_managed_codex_skills(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[str]:
        return self._host._validate_managed_codex_skills_impl(skill_names)

    def resolve_container_codex_skill_files(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[dict[str, Any]]:
        return self._host._resolve_container_codex_skill_files_impl(skill_names)
