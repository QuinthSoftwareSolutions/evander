from __future__ import annotations

import threading
from pathlib import Path

import pytest

from evander.admin.services import SkillAdminService
from evander.admin.services.skill_admin_service import (
    SkillDeletionBlockedByWorkflowReferences,
    SkillPropagationError,
)
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


def test_skill_admin_service_reports_partial_replacement_propagation(
    tmp_path: Path,
) -> None:
    class Session:
        def __init__(self, session_id: str, *, container: bool, skills: list[str]) -> None:
            self.session_id = session_id
            self.container = container
            self.metadata = {"codex_skills": skills}

    repository = SkillLibraryRepository(tmp_path)
    service = SkillAdminService(repository)
    service.create_skill({"name": "skill-a", "content": "# Original\n"})
    sessions = [
        Session("container-session", container=True, skills=["skill-a"]),
        Session("other-session", container=True, skills=["other-skill"]),
        Session("vm-session", container=False, skills=["skill-a"]),
    ]
    installed: list[tuple[str, str]] = []

    def install_agent_skills(
        session_id: str,
        *,
        backend: str,
        skill_names: list[str],
        vm_user: str,
    ) -> dict[str, object]:
        assert skill_names == ["skill-a"]
        assert vm_user == "agent"
        if session_id == "vm-session":
            raise RuntimeError("VM is offline")
        installed.append((session_id, backend))
        return {"success": True}

    service.configure_agent_skill_propagation(
        list_active_sessions=lambda: sessions,
        install_agent_skills=install_agent_skills,
        is_container_session=lambda session: session.container,
    )

    with pytest.raises(SkillPropagationError) as exc_info:
        service.create_skill({"name": "skill-a", "content": "# Updated\n", "replace": True})

    assert repository.get_skill("skill-a").content == "# Updated\n"
    assert installed == [("container-session", "container_worker_vm")]
    assert "Failed sessions: vm-session." in str(exc_info.value)
    assert exc_info.value.details == {
        "skill_name": "skill-a",
        "replacement_saved": True,
        "target_count": 2,
        "updated_session_ids": ["container-session"],
        "failures": [
            {
                "stage": "install_skill",
                "session_id": "vm-session",
                "backend": "libvirt_vm",
                "error_class": "RuntimeError",
                "error": "VM is offline",
            }
        ],
    }


def test_skill_admin_service_serializes_replacements_for_the_same_skill(
    tmp_path: Path,
) -> None:
    class Session:
        session_id = "container-session"
        metadata = {"codex_skills": ["skill-a"]}

    repository = SkillLibraryRepository(tmp_path)
    service = SkillAdminService(repository)
    service.create_skill({"name": "skill-a", "content": "# Original\n"})
    first_install_started = threading.Event()
    release_first_install = threading.Event()
    second_replace_attempted = threading.Event()
    second_install_completed = threading.Event()
    installed_contents: list[str] = []
    errors: list[Exception] = []

    def install_agent_skills(
        _session_id: str,
        *,
        backend: str,
        skill_names: list[str],
        vm_user: str,
    ) -> dict[str, object]:
        assert backend == "container_worker_vm"
        assert skill_names == ["skill-a"]
        assert vm_user == "agent"
        content = repository.get_skill("skill-a").content
        if content == "# First\n":
            first_install_started.set()
            if not release_first_install.wait(timeout=2):
                raise RuntimeError("Timed out waiting to finish the first installation")
        elif content == "# Second\n":
            second_install_completed.set()
        installed_contents.append(content)
        return {"success": True}

    service.configure_agent_skill_propagation(
        list_active_sessions=lambda: [Session()],
        install_agent_skills=install_agent_skills,
        is_container_session=lambda _session: True,
    )

    def replace(content: str) -> None:
        try:
            if content == "# Second\n":
                second_replace_attempted.set()
            service.create_skill({"name": "skill-a", "content": content, "replace": True})
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=replace, args=("# First\n",))
    second = threading.Thread(target=replace, args=("# Second\n",))
    first.start()
    assert first_install_started.wait(timeout=2)
    second.start()

    assert second_replace_attempted.wait(timeout=2)
    assert not second_install_completed.wait(timeout=0.1)
    assert repository.get_skill("skill-a").content == "# First\n"

    release_first_install.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert installed_contents == ["# First\n", "# Second\n"]
    assert repository.get_skill("skill-a").content == "# Second\n"


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
