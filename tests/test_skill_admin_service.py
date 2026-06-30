from __future__ import annotations

from pathlib import Path

import pytest

from evander.admin.services import SkillAdminService
from evander.repositories.skills_library import SkillLibraryRepository


def test_skill_admin_service_manages_skill_library(tmp_path: Path) -> None:
    service = SkillAdminService(SkillLibraryRepository(tmp_path))

    created = service.create_skill({"name": "skill-a", "content": "# Skill A"})

    assert created.name == "skill-a"
    assert [item.name for item in service.list_skills()] == ["skill-a"]
    assert service.get_skill("skill-a").content == "# Skill A"
    assert service.validate_managed_codex_skills(["skill-a", "skill-a"]) == ["skill-a"]
    assert service.resolve_container_codex_skill_files(["skill-a"]) == [
        {
            "destination_path": "/home/agent/.config/evander/codex/skills/skill-a/SKILL.md",
            "mode": "0644",
            "content": "# Skill A",
        }
    ]
    assert service.delete_skill("skill-a").name == "skill-a"
    assert service.list_skills() == []


def test_skill_admin_service_validates_create_payload(tmp_path: Path) -> None:
    service = SkillAdminService(SkillLibraryRepository(tmp_path))

    with pytest.raises(ValueError, match="Skill name is required"):
        service.create_skill({"content": "# Missing name"})

    with pytest.raises(ValueError, match="Skill content is required"):
        service.create_skill({"name": "missing-content"})


def test_skill_admin_service_rejects_unknown_managed_skill(tmp_path: Path) -> None:
    service = SkillAdminService(SkillLibraryRepository(tmp_path))

    with pytest.raises(ValueError, match="Unknown Codex skill: missing"):
        service.validate_managed_codex_skills(["missing"])
