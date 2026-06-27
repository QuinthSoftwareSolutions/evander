from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent_sandbox.admin.service import WorkflowEditorGeneratedDraftPayload


class _WorkflowGenerationAdminServiceHost(Protocol):
    async def _generate_workflow_editor_draft_public_impl(
        self,
        *,
        description: str,
        directory_path: str | None = None,
        include_current_packs: bool = True,
    ) -> "WorkflowEditorGeneratedDraftPayload": ...


class WorkflowGenerationAdminService:
    """Workflow draft generation operations extracted from AdminService."""

    def __init__(self, host: _WorkflowGenerationAdminServiceHost) -> None:
        self._host = host

    async def generate_workflow_editor_draft(
        self,
        *,
        description: str,
        directory_path: str | None = None,
        include_current_packs: bool = True,
    ) -> "WorkflowEditorGeneratedDraftPayload":
        return await self._host._generate_workflow_editor_draft_public_impl(
            description=description,
            directory_path=directory_path,
            include_current_packs=include_current_packs,
        )
