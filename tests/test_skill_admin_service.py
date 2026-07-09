from __future__ import annotations

from pathlib import Path

import pytest

from evander.admin.services import SkillAdminService
from evander.admin.services.skill_admin_service import SkillDeletionBlockedByWorkflowReferences
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


def test_skill_admin_service_blocks_delete_when_workflow_references_skill(
    tmp_path: Path,
) -> None:
    class ReferencingWorkflowConfig:
        def find_managed_codex_skill_references(self, skill_name: str):
            return type(
                "Result",
                (),
                {
                    "references": [
                        {
                            "scope": "inline",
                            "path": "/config/trigger-orchestrator/config.json",
                            "node_id": "create-agent",
                            "node_type": "effect:agent:create",
                            "display_name": "Create agent",
                            "field_path": "codex_skills",
                            "reference_count": 1,
                        }
                    ],
                },
            )()

    repository = SkillLibraryRepository(tmp_path)
    service = SkillAdminService(
        repository,
        workflow_config_admin=ReferencingWorkflowConfig(),
    )
    service.create_skill({"name": "skill-a", "content": "# Skill A"})

    with pytest.raises(SkillDeletionBlockedByWorkflowReferences) as exc_info:
        service.delete_skill("skill-a")

    assert repository.has_skill("skill-a")
    assert exc_info.value.details["skill_name"] == "skill-a"
    assert exc_info.value.details["references"][0]["node_id"] == "create-agent"
