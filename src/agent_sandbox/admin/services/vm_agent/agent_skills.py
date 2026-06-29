from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.common import (
    Any,
    SkillLibraryEntry,
    UTC,
    datetime,
    normalize_skill_names,
)


class VmAgentSkillInstallMixin:
    def _record_installed_codex_skills(self, session_id: str, skill_names: list[str]) -> None:
        session = self.runtime.session_repository.get_session(session_id)
        if session is None:
            return
        metadata = dict(session.metadata)
        existing = metadata.get("codex_skills")
        merged: list[str] = []
        if isinstance(existing, list):
            for item in existing:
                if isinstance(item, str) and item.strip():
                    merged.append(item)
        merged.extend(skill_names)
        metadata["codex_skills"] = normalize_skill_names(merged)
        metadata["codex_skills_updated_at"] = datetime.now(UTC).isoformat()
        self.runtime.session_repository.upsert_session(
            session.model_copy(update={"metadata": metadata})
        )

    @staticmethod
    def _skill_install_files_for_codex_home(
        entries: list[SkillLibraryEntry], *, codex_home: str
    ) -> list[dict[str, Any]]:
        normalized_home = str(codex_home or "").strip().rstrip("/")
        if (
            not normalized_home
            or not normalized_home.startswith("/")
            or "\n" in normalized_home
            or "\x00" in normalized_home
        ):
            raise RuntimeError(f"Guest CODEX_HOME resolved to an invalid path: {codex_home!r}")
        return [
            {
                "destination_path": f"{normalized_home}/skills/{entry.name}/SKILL.md",
                "mode": "0644",
                "content": entry.content,
                "overwrite": True,
            }
            for entry in entries
        ]

    def _install_codex_skills_into_vm_agent(
        self,
        agent_id: str,
        *,
        skill_names: list[str],
        vm_user: str,
    ) -> dict[str, Any]:
        session = self._resolve_vm_agent_session(agent_id)
        target = session.session_id
        target_name = str(session.vm_id or agent_id).strip()
        if not target_name:
            raise ValueError(f"Session {session.session_id!r} is not linked to a VM")
        entries = [self.skill_library.get_skill(name) for name in skill_names]
        codex_home_result = self.vm_exec(
            target,
            command='printf "%s" "${CODEX_HOME:-$HOME/.config/agent-sandbox/codex}"',
            vm_user=vm_user,
            timeout_seconds=30,
            check=True,
        )
        if not bool(codex_home_result.get("success")):
            message = str(codex_home_result.get("message") or "Failed to resolve guest CODEX_HOME")
            raise RuntimeError(message)
        codex_home = str(codex_home_result.get("stdout") or "").strip()
        files = self._skill_install_files_for_codex_home(entries, codex_home=codex_home)
        result = self.vm_copy_files(target, files=files, vm_user=vm_user)
        if not bool(result.get("success")):
            message = str(result.get("message") or "Failed to copy Codex skills")
            raise RuntimeError(message)
        copied_paths = list(result.get("copied_paths") or [])
        self._record_installed_codex_skills(session.session_id, skill_names)
        return {
            "success": True,
            "agent_id": agent_id,
            "backend": "libvirt_vm",
            "target_name": target_name,
            "action": "install-skills",
            "message": (
                f"Installed {len(skill_names)} Codex skill"
                f"{'' if len(skill_names) == 1 else 's'} into session "
                f"{session.session_id}: "
                f"{', '.join(skill_names)}"
            ),
            "status": "updated",
            "session_id": session.session_id,
            "copied_count": len(copied_paths),
            "copied_paths": copied_paths,
            "codex_skills": list(skill_names),
        }

    def _install_codex_skills_into_container_agent(
        self,
        agent_id: str,
        *,
        skill_names: list[str],
    ) -> dict[str, Any]:
        session, record = self._resolve_container_agent_session(agent_id, require_session=True)
        if session is None:
            raise ValueError(f"Unknown container worker agent target: {agent_id}")
        execution_records = self._container_execution_records_for_session(session.session_id)
        worker_backend = self.container_worker_backend(
            directory_path=session.directory_path,
            worker_url=self._container_worker_url_for_target(
                session=session,
                record=record,
                records=execution_records,
            ),
        )
        files = self.resolve_container_codex_skill_files(skill_names)
        result = worker_backend.write_files(session_id=session.session_id, files=files)
        copied_paths = list(getattr(result, "copied_paths", []) or [])
        self._record_installed_codex_skills(session.session_id, skill_names)
        return {
            "success": True,
            "agent_id": agent_id,
            "backend": "container_worker_vm",
            "target_name": self._container_session_target_name(session),
            "action": "install-skills",
            "message": (
                f"Installed {len(skill_names)} Codex skill"
                f"{'' if len(skill_names) == 1 else 's'} into session "
                f"{session.session_id}: "
                f"{', '.join(skill_names)}"
            ),
            "status": "updated",
            "session_id": session.session_id,
            "copied_count": int(getattr(result, "copied_count", len(copied_paths)) or 0),
            "copied_paths": copied_paths,
            "codex_skills": list(skill_names),
        }

    def install_admin_agent_skills(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        skill_names: list[str] | tuple[str, ...] | None = None,
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        names = self.validate_managed_codex_skills(skill_names)
        if not names:
            raise ValueError("codex_skills must contain at least one skill")
        selected_backend = self._normalize_agent_backend(backend)
        if selected_backend == "libvirt_vm":
            return self._install_codex_skills_into_vm_agent(
                agent_id,
                skill_names=names,
                vm_user=vm_user,
            )
        return self._install_codex_skills_into_container_agent(agent_id, skill_names=names)

    record_installed_codex_skills = _record_installed_codex_skills
    skill_install_files_for_codex_home = _skill_install_files_for_codex_home
    install_codex_skills_into_vm_agent = _install_codex_skills_into_vm_agent
    install_codex_skills_into_container_agent = _install_codex_skills_into_container_agent
