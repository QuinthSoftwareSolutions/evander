from __future__ import annotations

from typing import Any


class WorkflowConfigAdminService:
    """Workflow and worker-config admin operations.

    This service owns the workflow-facing API surface and composes the worker
    admin implementation.
    """

    def __init__(self, worker_admin: Any) -> None:
        self._worker_admin = worker_admin

    def read_worker_global_config(self, worker_id: str):
        return self._worker_admin.read_worker_global_config(worker_id)

    def write_worker_global_config(self, worker_id: str, payload: dict[str, object]):
        return self._worker_admin.write_worker_global_config(worker_id, payload)

    def read_workflow_editor_document(self):
        return self._worker_admin.read_workflow_editor_document()

    def read_workflow_editor_catalog(self):
        return self._worker_admin.read_workflow_editor_catalog()

    def validate_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
    ):
        return self._worker_admin.validate_workflow_editor_document(config=config, layout=layout)

    def save_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
    ):
        return self._worker_admin.save_workflow_editor_document(config=config, layout=layout)

    def read_workflow_run_snapshot(self):
        return self._worker_admin.read_workflow_run_snapshot()

    def clear_workflow_run(self, event_id: str):
        return self._worker_admin.clear_workflow_run(event_id)

    def resume_workflow_run(self, event_id: str):
        return self._worker_admin.resume_workflow_run(event_id)

    def workflow_event_journal(self):
        return self._worker_admin.workflow_event_journal()

    def upsert_trigger_config(self, trigger_id: str, payload: dict[str, object]):
        return self._worker_admin.upsert_trigger_config(trigger_id, payload)

    def delete_trigger_config(self, trigger_id: str):
        return self._worker_admin.delete_trigger_config(trigger_id)

    def upsert_effect_config(self, effect_id: str, payload: dict[str, object]):
        return self._worker_admin.upsert_effect_config(effect_id, payload)

    def delete_effect_config(self, effect_id: str):
        return self._worker_admin.delete_effect_config(effect_id)
