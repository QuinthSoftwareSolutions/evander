from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_sandbox.repositories.skills_library import (
    SkillLibraryRepository,
    normalize_skill_names,
)


def managed_codex_skill_names(
    *,
    context: Any,
    config: Any,
    skill_names: list[str] | tuple[str, ...] | None,
) -> list[str]:
    names = normalize_skill_names(skill_names)
    if not names:
        return []
    validator = getattr(
        getattr(context, "admin_service", None), "validate_managed_codex_skills", None
    )
    if callable(validator):
        return validator(names)
    repository = SkillLibraryRepository(Path(config.runtime_dir))
    for name in names:
        if not repository.has_skill(name):
            raise ValueError(f"Unknown Codex skill: {name}")
    return names


def container_codex_skill_files(
    *,
    context: Any,
    config: Any,
    skill_names: list[str] | tuple[str, ...] | None,
) -> list[dict[str, Any]]:
    names = normalize_skill_names(skill_names)
    if not names:
        return []
    resolver = getattr(
        getattr(context, "admin_service", None),
        "resolve_container_codex_skill_files",
        None,
    )
    if callable(resolver):
        return resolver(names)
    return SkillLibraryRepository(Path(config.runtime_dir)).container_file_specs(names)


__all__ = ["container_codex_skill_files", "managed_codex_skill_names"]
