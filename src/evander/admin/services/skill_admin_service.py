from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any

from evander.repositories.skills_library import (
    SkillLibraryEntry,
    SkillLibraryMetadata,
    SkillLibraryRepository,
    codex_skill_install_vm_user,
    normalize_skill_names,
    validate_skill_name,
)


class SkillDeletionBlockedByWorkflowReferences(ValueError):
    def __init__(self, skill_name: str, references: list[dict[str, object]]) -> None:
        super().__init__(f"Cannot delete Codex skill {skill_name}: it is referenced by workflows.")
        self.skill_name = skill_name
        self.references = references

    @property
    def details(self) -> dict[str, object]:
        return {
            "skill_name": self.skill_name,
            "references": self.references,
        }


class SkillPropagationError(RuntimeError):
    def __init__(
        self,
        skill_name: str,
        *,
        target_count: int | None,
        updated_session_ids: list[str],
        failures: list[dict[str, object]],
    ) -> None:
        if target_count is None:
            message = (
                f"Managed skill {skill_name} was replaced, but active agent sessions "
                "could not be discovered. The replacement remains saved; retry Replace "
                "to reconcile installed copies."
            )
        else:
            failed_session_ids = [
                str(failure.get("session_id") or "").strip()
                for failure in failures
                if str(failure.get("session_id") or "").strip()
            ]
            failed_sessions = (
                f" Failed sessions: {', '.join(failed_session_ids)}." if failed_session_ids else ""
            )
            message = (
                f"Managed skill {skill_name} was replaced, but automatic propagation "
                f"failed for {len(failures)} of {target_count} active agent sessions. "
                "The replacement remains saved; retry Replace to reconcile the failed sessions."
                f"{failed_sessions}"
            )
        super().__init__(message)
        self.skill_name = skill_name
        self.target_count = target_count
        self.updated_session_ids = updated_session_ids
        self.failures = failures

    @property
    def details(self) -> dict[str, object]:
        return {
            "skill_name": self.skill_name,
            "replacement_saved": True,
            "target_count": self.target_count,
            "updated_session_ids": self.updated_session_ids,
            "failures": self.failures,
        }


class SkillAdminService:
    """Admin service for managed Codex skill-library operations."""

    def __init__(
        self,
        skill_library: SkillLibraryRepository,
        *,
        workflow_config_admin: Any | None = None,
    ) -> None:
        self._skill_library = skill_library
        self._workflow_config_admin = workflow_config_admin
        self._list_active_sessions: Callable[[], list[Any]] | None = None
        self._install_agent_skills: Callable[..., Mapping[str, Any]] | None = None
        self._is_container_session: Callable[[Any], bool] | None = None
        self._replacement_locks_guard = threading.Lock()
        self._replacement_locks: dict[str, threading.Lock] = {}

    def configure_agent_skill_propagation(
        self,
        *,
        list_active_sessions: Callable[[], list[Any]],
        install_agent_skills: Callable[..., Mapping[str, Any]],
        is_container_session: Callable[[Any], bool],
    ) -> None:
        self._list_active_sessions = list_active_sessions
        self._install_agent_skills = install_agent_skills
        self._is_container_session = is_container_session

    def list_skills(self) -> list[SkillLibraryMetadata]:
        return self._skill_library.list_skills()

    def create_skill(self, payload: Mapping[str, Any]) -> SkillLibraryMetadata:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("Skill name is required.")
        if "content" not in payload:
            raise ValueError("Skill content is required.")
        normalized_name = validate_skill_name(name)
        replace = bool(payload.get("replace", False))
        if not replace:
            return self._skill_library.create_skill(
                name=normalized_name,
                content=payload.get("content"),
                replace=False,
            )
        with self._replacement_lock(normalized_name):
            existed = self._skill_library.has_skill(normalized_name)
            metadata = self._skill_library.create_skill(
                name=normalized_name,
                content=payload.get("content"),
                replace=True,
            )
            if existed:
                self._propagate_replaced_skill(metadata.name)
            return metadata

    def _replacement_lock(self, skill_name: str) -> threading.Lock:
        with self._replacement_locks_guard:
            lock = self._replacement_locks.get(skill_name)
            if lock is None:
                lock = threading.Lock()
                self._replacement_locks[skill_name] = lock
            return lock

    def _propagate_replaced_skill(self, skill_name: str) -> None:
        if (
            self._list_active_sessions is None
            or self._install_agent_skills is None
            or self._is_container_session is None
        ):
            return

        try:
            sessions = sorted(
                self._list_active_sessions(),
                key=lambda session: str(getattr(session, "session_id", "")),
            )
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            raise SkillPropagationError(
                skill_name,
                target_count=None,
                updated_session_ids=[],
                failures=[
                    {
                        "stage": "discover_sessions",
                        "error_class": type(exc).__name__,
                        "error": detail,
                    }
                ],
            ) from exc

        targets = [
            session for session in sessions if self._session_declares_skill(session, skill_name)
        ]
        updated_session_ids: list[str] = []
        failures: list[dict[str, object]] = []
        for session in targets:
            session_id = str(getattr(session, "session_id", "") or "").strip()
            backend = "container_worker_vm" if self._is_container_session(session) else "libvirt_vm"
            metadata = getattr(session, "metadata", None)
            vm_user = codex_skill_install_vm_user(metadata, skill_name)
            try:
                result = self._install_agent_skills(
                    session_id,
                    backend=backend,
                    skill_names=[skill_name],
                    vm_user=vm_user,
                )
                if result.get("success") is False:
                    raise RuntimeError(
                        str(result.get("message") or "Skill installation was unsuccessful.")
                    )
            except Exception as exc:
                detail = str(exc).strip() or type(exc).__name__
                failures.append(
                    {
                        "stage": "install_skill",
                        "session_id": session_id,
                        "backend": backend,
                        "error_class": type(exc).__name__,
                        "error": detail,
                    }
                )
            else:
                updated_session_ids.append(session_id)

        if failures:
            raise SkillPropagationError(
                skill_name,
                target_count=len(targets),
                updated_session_ids=updated_session_ids,
                failures=failures,
            )

    @staticmethod
    def _session_declares_skill(session: Any, skill_name: str) -> bool:
        metadata = getattr(session, "metadata", None)
        if not isinstance(metadata, Mapping):
            return False
        installed = metadata.get("codex_skills")
        if not isinstance(installed, (list, tuple)):
            return False
        return any(str(item or "").strip() == skill_name for item in installed)

    def get_skill(self, name: str) -> SkillLibraryEntry:
        return self._skill_library.get_skill(name)

    def delete_skill(self, name: str) -> SkillLibraryMetadata:
        normalized_name = validate_skill_name(name)
        if not self._skill_library.has_skill(normalized_name):
            raise LookupError(f"Unknown skill: {normalized_name}")
        reference_finder = getattr(
            self._workflow_config_admin,
            "find_managed_codex_skill_references",
            None,
        )
        if callable(reference_finder):
            result = reference_finder(normalized_name)
            references = list(getattr(result, "references", []) or [])
            if references:
                raise SkillDeletionBlockedByWorkflowReferences(
                    normalized_name,
                    references,
                )
        return self._skill_library.delete_skill(normalized_name)

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
