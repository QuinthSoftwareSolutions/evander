from __future__ import annotations

from agent_sandbox.admin.services.ports import WorkflowConfigPort


class WorkflowConfigAdminService:
    """Workflow and worker-config admin operations.

    This service owns the workflow-facing API surface and composes the worker
    admin implementation.
    """

    def __init__(self, worker_admin: WorkflowConfigPort) -> None:
        self._worker_admin = worker_admin

    def read_worker_global_config(self, worker_id: str):
        return self._worker_admin.read_worker_global_config(worker_id)

    def write_worker_global_config(
        self,
        worker_id: str,
        payload: dict[str, object],
        *,
        expected_revision: str | None = None,
    ):
        return self._worker_admin.write_worker_global_config(
            worker_id, payload, expected_revision=expected_revision
        )

    def read_workflow_editor_document(self, workflow_pack: str | None = None):
        if workflow_pack is None:
            return self._worker_admin.read_workflow_editor_document()
        return self._worker_admin.read_workflow_editor_document(
            workflow_pack=workflow_pack
        )

    def read_composed_workflow_editor_document(self):
        return self._worker_admin.read_composed_workflow_editor_document()

    def read_workflow_editor_catalog(self, directory_path: str | None = None):
        return self._worker_admin.read_workflow_editor_catalog(
            directory_path=directory_path
        )

    def validate_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        workflow_pack: str | None = None,
    ):
        if workflow_pack is None:
            return self._worker_admin.validate_workflow_editor_document(
                config=config, layout=layout, graphs=graphs
            )
        return self._worker_admin.validate_workflow_editor_document(
            config=config, layout=layout, graphs=graphs, workflow_pack=workflow_pack
        )

    def save_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        expected_revision: str | None = None,
        workflow_pack: str | None = None,
    ):
        if workflow_pack is None:
            return self._worker_admin.save_workflow_editor_document(
                config=config,
                layout=layout,
                graphs=graphs,
                expected_revision=expected_revision,
            )
        return self._worker_admin.save_workflow_editor_document(
            config=config,
            layout=layout,
            graphs=graphs,
            expected_revision=expected_revision,
            workflow_pack=workflow_pack,
        )

    def read_workflow_run_snapshot(self):
        return self._worker_admin.read_workflow_run_snapshot()

    def read_workflow_run_detail(
        self,
        *,
        run_id: str | None = None,
        event_id: str | None = None,
        trigger_event_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        artifact_id: str | None = None,
    ):
        return self._worker_admin.read_workflow_run_detail(
            run_id=run_id,
            event_id=event_id,
            trigger_event_id=trigger_event_id,
            trace_id=trace_id,
            session_id=session_id,
            artifact_id=artifact_id,
        )

    def read_workflow_run_search(
        self,
        *,
        node_id: str | None = None,
        graph_id: str | None = None,
        without_session: bool = False,
        include_completed: bool = True,
        limit: int | None = None,
    ):
        return self._worker_admin.read_workflow_run_search(
            node_id=node_id,
            graph_id=graph_id,
            without_session=without_session,
            include_completed=include_completed,
            limit=limit,
        )

    def clear_workflow_run(self, event_id: str):
        return self._worker_admin.clear_workflow_run(event_id)

    def dismiss_workflow_node_failures(self, node_id: str):
        return self._worker_admin.dismiss_workflow_node_failures(node_id)

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
