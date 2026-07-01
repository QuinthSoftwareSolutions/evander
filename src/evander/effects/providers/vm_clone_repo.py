from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.events.provider_models import VmCloneRepoEffectDefinition
from evander.git_pr_notes import agent_pr_notes_ref_for_session

from .base import EffectProviderBase
from .vm_targeting import VmTargetingMixin


class VmCloneRepoEffectProvider(VmTargetingMixin, EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "repo_source": effect.repo_source,
            "clone_branch": effect.clone_branch,
            "vm_user": effect.vm_user,
            "directory_path": effect.directory_path,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        context = self._context()
        repo_cloner = getattr(context.vm_repo_cloner, "clone_repo_into_vm", None)
        if not callable(repo_cloner):
            raise RuntimeError("effect:vm:clone-repo vm_repo_cloner is not available")

        vm_name, session_id = self._resolve_target(payload, effect_type=self._effect_type())
        result = repo_cloner(
            vm_name=vm_name,
            session_id=session_id,
            repo_source=self._required_string(
                payload.get("repo_source"), "effect:vm:clone-repo requires repo_source"
            ),
            clone_branch=self._normalize_optional_string(payload.get("clone_branch")),
            vm_user=self._normalize_optional_string(payload.get("vm_user")) or "agent",
            directory_path=self._normalize_optional_string(payload.get("directory_path"))
            or str(context.metadata.get("directory_path") or "root"),
        )
        return {
            "success": bool(result.success),
            "message": self._normalize_optional_string(getattr(result, "message", None)) or "",
            "session_id": session_id,
            "vm_name": result.vm_name or vm_name,
            "target_dir": self._normalize_optional_string(getattr(result, "target_dir", None)),
            "guest_repo_name": self._normalize_optional_string(
                getattr(result, "guest_repo_name", None)
            ),
            "remote_url": self._normalize_optional_string(getattr(result, "remote_url", None)),
            "pr_notes_ref": agent_pr_notes_ref_for_session(session_id),
        }

    def _effect(self) -> VmCloneRepoEffectDefinition:
        if not isinstance(self.effect_definition, VmCloneRepoEffectDefinition):
            raise RuntimeError(
                "VmCloneRepoEffectProvider initialized with a non-vm-clone-repo effect definition"
            )
        return self.effect_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("VmCloneRepoEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _effect_type() -> str:
        return "effect:vm:clone-repo"
