from __future__ import annotations

from pathlib import Path

import pytest

from agent_sandbox.repositories.skills_library import SkillLibraryRepository


def test_skill_library_create_list_read_replace_delete(tmp_path: Path) -> None:
    repository = SkillLibraryRepository(tmp_path)

    created = repository.skill_admin.create_skill(
        name="demo.skill",
        content="# Demo\n\nUse this skill.",
    )

    assert created.name == "demo.skill"
    assert created.size_bytes == len("# Demo\n\nUse this skill.".encode("utf-8"))
    assert [item.name for item in repository.list_skills()] == ["demo.skill"]
    assert repository.get_skill("demo.skill").content == "# Demo\n\nUse this skill."
    assert (tmp_path / "skills" / "demo.skill" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# Demo\n\nUse this skill."

    replaced = repository.skill_admin.create_skill(
        name="demo.skill",
        content="# Updated",
        replace=True,
    )

    assert replaced.name == "demo.skill"
    assert repository.get_skill("demo.skill").content == "# Updated"

    deleted = repository.delete_skill("demo.skill")

    assert deleted.name == "demo.skill"
    assert repository.list_skills() == []
    assert not (tmp_path / "skills" / "demo.skill" / "SKILL.md").exists()


def test_skill_library_rejects_duplicates_without_replace(tmp_path: Path) -> None:
    repository = SkillLibraryRepository(tmp_path)
    repository.skill_admin.create_skill(name="demo", content="# Demo")

    with pytest.raises(FileExistsError, match="Skill already exists: demo"):
        repository.skill_admin.create_skill(name="demo", content="# Other")


@pytest.mark.parametrize("name", ["", "../escape", "nested/path", "nested\\path", ".", ".."])
def test_skill_library_rejects_invalid_names(tmp_path: Path, name: str) -> None:
    repository = SkillLibraryRepository(tmp_path)

    with pytest.raises(ValueError):
        repository.skill_admin.create_skill(name=name, content="# Demo")


def test_skill_library_rejects_empty_content(tmp_path: Path) -> None:
    repository = SkillLibraryRepository(tmp_path)

    with pytest.raises(ValueError, match="must not be empty"):
        repository.skill_admin.create_skill(name="demo", content=" \n")


def test_skill_library_container_file_specs_fail_for_unknown_skill(tmp_path: Path) -> None:
    repository = SkillLibraryRepository(tmp_path)

    with pytest.raises(LookupError, match="Unknown skill: missing"):
        repository.container_file_specs(["missing"])
