from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    AgentRecord,
    Any,
    ContainerSessionLifecycleState,
    Mapping,
    SessionManifest,
    SessionSummary,
    _coerce_optional_string,
    _normalize_session_labels,
    re,
)


class VmAgentContainerRecordInventoryMixin:
    def _reconcile_container_execution_records(self, records: list[Any]) -> list[Any]:
        reconciled: list[Any] = []
        for record in records:
            status = self._container_agent_status(record.last_status)
            if status not in {"starting", "running", "unknown"}:
                reconciled.append(record)
                continue
            try:
                self.container_worker_backend(
                    directory_path=record.directory_path,
                    worker_url=self._container_worker_url_for_target(record=record),
                ).inspect(record.execution_id)
                record = (
                    self._container_worker_execution_repository().get(record.execution_id) or record
                )
            except Exception:
                pass
            reconciled.append(record)
        return reconciled

    def _container_execution_payload(self, record: Any) -> dict[str, Any]:
        status = self._container_execution_status(record)
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        image = metadata.get("image")
        return {
            "execution_id": record.execution_id,
            "session_id": record.session_id,
            "worker_job_id": record.worker_job_id,
            "worker_url": record.worker_url,
            "target_name": record.target_name,
            "status": status,
            "last_status": record.last_status,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "updated_at": record.updated_at,
            "image": image if isinstance(image, str) and image.strip() else None,
            "metadata": metadata,
        }

    def _container_worker_image_details(
        self,
        metadata: Mapping[str, Any] | None,
        *,
        fallback_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, str | None]:
        container_worker = (
            metadata.get("container_worker") if isinstance(metadata, Mapping) else None
        )
        image: str | None = None
        image_alias: str | None = None
        if isinstance(container_worker, Mapping):
            raw_image = container_worker.get("image")
            if isinstance(raw_image, str):
                image = raw_image.strip() or None
            raw_alias = container_worker.get("image_alias")
            if isinstance(raw_alias, str):
                image_alias = raw_alias.strip() or None
        if image is None and isinstance(fallback_metadata, Mapping):
            raw_fallback_image = fallback_metadata.get("image")
            if isinstance(raw_fallback_image, str):
                image = raw_fallback_image.strip() or None
        return {"image": image, "image_alias": image_alias}

    @staticmethod
    def _runtime_image_ref_display_details(image_ref: str) -> tuple[str, bool]:
        display_ref = image_ref.strip()
        if not display_ref:
            return "", False

        sanitized = False
        candidate = display_ref

        if candidate.startswith("file://"):
            candidate = candidate.removeprefix("file://")
            sanitized = True
        if candidate.startswith("~/"):
            candidate = candidate[2:]
            sanitized = True
        elif candidate.startswith("~\\"):
            candidate = candidate[2:]
            sanitized = True
        if re.match(r"^[A-Za-z]:[\\/]", candidate):
            candidate = candidate[2:]
            sanitized = True
        if "/" in candidate or "\\" in candidate:
            candidate = candidate.replace("\\", "/").rstrip("/")
            candidate = candidate.split("/")[-1]
            sanitized = True
        return candidate or display_ref, sanitized

    def session_runtime_image_payload(
        self, session: SessionManifest | SessionSummary
    ) -> dict[str, Any]:
        if self._is_container_session(session):
            image_details = self._container_worker_image_details(
                dict(session.metadata) if isinstance(session.metadata, dict) else {}
            )
            payload: dict[str, Any] = {
                "backend": "container_worker_vm",
                "kind": "container_worker_image",
                "label": "Container worker image",
                "image_alias": image_details["image_alias"],
                "image_ref": image_details["image"],
                "missing_reason": None,
            }
            if image_details["image"] is None:
                payload["missing_reason"] = "container_worker_image_missing"
            return payload

        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        template = metadata.get("libvirt_vm_template")
        if not isinstance(template, Mapping):
            return {
                "backend": "libvirt_vm",
                "kind": "libvirt_vm_template",
                "label": "Legacy session: VM template metadata unavailable",
                "template_alias": None,
                "image_ref": None,
                "image_ref_sanitized": False,
                "missing_reason": "legacy_missing_libvirt_vm_template",
            }

        raw_image_ref = template.get("image_ref")
        if not isinstance(raw_image_ref, str):
            return {
                "backend": "libvirt_vm",
                "kind": "libvirt_vm_template",
                "label": "Legacy session: VM template metadata unavailable",
                "template_alias": None,
                "image_ref": None,
                "image_ref_sanitized": False,
                "missing_reason": "legacy_missing_libvirt_vm_template",
            }

        image_ref = raw_image_ref.strip()
        if not image_ref:
            return {
                "backend": "libvirt_vm",
                "kind": "libvirt_vm_template",
                "label": "Legacy session: VM template metadata unavailable",
                "template_alias": None,
                "image_ref": None,
                "image_ref_sanitized": False,
                "missing_reason": "legacy_missing_libvirt_vm_template",
            }

        display_image_ref, image_ref_sanitized = self._runtime_image_ref_display_details(image_ref)
        raw_template_alias = template.get("template_alias")
        template_alias = (
            _coerce_optional_string(raw_template_alias)
            if isinstance(raw_template_alias, str)
            else None
        )
        return {
            "backend": "libvirt_vm",
            "kind": "libvirt_vm_template",
            "label": "Libvirt VM template",
            "template_alias": template_alias,
            "image_ref": display_image_ref,
            "image_ref_sanitized": image_ref_sanitized,
            "missing_reason": None,
        }

    def _agent_record_from_container_execution(
        self,
        record: Any,
        *,
        reconcile: bool,
        include_effective_tool_policy: bool = True,
    ) -> AgentRecord:
        status = self._container_execution_status(record)
        message: str | None = None
        session = self.runtime.session_repository.get_session(record.session_id)
        if reconcile:
            try:
                self.container_worker_backend(
                    directory_path=record.directory_path,
                    worker_url=self._container_worker_url_for_target(record=record),
                ).inspect(record.execution_id)
            except Exception as exc:
                message = str(exc)
            else:
                record = self._container_worker_execution_repository().resolve(record.execution_id)
                status = self._container_execution_status(record)
        host = {
            "kind": "container_worker",
            "worker_job_id": record.worker_job_id,
            "worker_url": record.worker_url,
            "last_status": record.last_status,
            "metadata": dict(record.metadata),
            "session_policy": self._agent_session_policy_payload(session),
        }
        if message:
            host["reconcile_error"] = message
        image_details = self._container_worker_image_details(
            dict(session.metadata) if isinstance(session.metadata, dict) else {},
            fallback_metadata=record.metadata if isinstance(record.metadata, Mapping) else None,
        )
        if image_details["image"] is not None:
            host["image"] = image_details["image"]
        if image_details["image_alias"] is not None:
            host["image_alias"] = image_details["image_alias"]
        return AgentRecord(
            agent_id=record.execution_id,
            backend="container_worker_vm",
            target_name=record.target_name,
            session_id=record.session_id,
            directory_path=self._agent_record_directory_path(record.directory_path),
            status=status,
            started_at=record.started_at,
            finished_at=record.finished_at,
            updated_at=record.updated_at,
            effective_tool_policy=(
                self.effective_tool_policy_payload(session)
                if include_effective_tool_policy
                else None
            ),
            host=host,
            capabilities={
                "start": False,
                "shutdown": False,
                "resume": False,
                "enable_disable": False,
                "kill": False,
                "logs": True,
                "stop": True,
                "destroy": True,
            },
        )

    def _agent_record_from_container_session(
        self,
        session: SessionManifest,
        *,
        executions: list[Any] | None = None,
        execution_summary: Any | None = None,
        reconcile: bool = False,
        include_effective_tool_policy: bool = True,
        include_execution_history: bool = True,
    ) -> AgentRecord:
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        target_name = self._container_session_target_name(session)
        labels = _normalize_session_labels(metadata.get("labels"))
        execution_records = sorted(
            list(executions or []),
            key=self._container_execution_sort_key,
            reverse=True,
        )
        if reconcile and execution_records:
            execution_records = sorted(
                self._reconcile_container_execution_records(execution_records),
                key=self._container_execution_sort_key,
                reverse=True,
            )
        summary_latest_execution = getattr(execution_summary, "latest_execution", None)
        latest_execution = execution_records[0] if execution_records else summary_latest_execution
        execution_count = (
            int(getattr(execution_summary, "execution_count", 0) or 0)
            if execution_summary is not None
            else len(execution_records)
        )
        active_count = (
            int(getattr(execution_summary, "active_execution_count", 0) or 0)
            if execution_summary is not None
            else sum(
                1
                for record in execution_records
                if self._agent_status_is_active(self._container_agent_status(record.last_status))
            )
        )
        active_running_count = int(getattr(execution_summary, "active_running_count", 0) or 0)
        execution_payloads = (
            [self._container_execution_payload(record) for record in execution_records]
            if include_execution_history
            else []
        )
        lifecycle_records = execution_records or ([latest_execution] if latest_execution else [])
        lifecycle = self._container_session_lifecycle(session, executions=lifecycle_records)
        if execution_summary is not None and active_count > 0:
            status = "running" if active_running_count > 0 else "starting"
        else:
            status = self._container_session_status(lifecycle_records)
        if lifecycle is not None and lifecycle.state in {
            ContainerSessionLifecycleState.ABANDONED,
            ContainerSessionLifecycleState.CLEANUP_FAILED,
            ContainerSessionLifecycleState.CLEANED_UP,
        }:
            status = lifecycle.state.value
        host: dict[str, Any] = {
            "kind": "container_worker_session",
            "metadata": metadata,
            "session_policy": self._agent_session_policy_payload(session),
            "execution_count": execution_count,
            "active_execution_count": active_count,
        }
        if include_execution_history:
            host["executions"] = execution_payloads
        latest_metadata: dict[str, Any] = {}
        if latest_execution is not None:
            latest_metadata = (
                dict(latest_execution.metadata)
                if isinstance(latest_execution.metadata, dict)
                else {}
            )
            host.update(
                {
                    "latest_execution_id": latest_execution.execution_id,
                    "worker_job_id": latest_execution.worker_job_id,
                    "worker_url": latest_execution.worker_url,
                    "last_status": latest_execution.last_status,
                }
            )
        image_details = self._container_worker_image_details(
            metadata,
            fallback_metadata=latest_metadata,
        )
        if image_details["image"] is not None:
            host["image"] = image_details["image"]
        if image_details["image_alias"] is not None:
            host["image_alias"] = image_details["image_alias"]
        if labels:
            host["labels"] = labels
        return AgentRecord(
            agent_id=session.session_id,
            backend="container_worker_vm",
            target_name=target_name,
            session_id=session.session_id,
            directory_path=self._agent_record_directory_path(session.directory_path),
            status=status,
            started_at=None,
            finished_at=None,
            updated_at=(
                latest_execution.updated_at if latest_execution is not None else session.created_at
            ),
            effective_tool_policy=(
                self.effective_tool_policy_payload(session)
                if include_effective_tool_policy
                else None
            ),
            host=host,
            capabilities={
                "start": False,
                "shutdown": False,
                "resume": False,
                "enable_disable": False,
                "kill": False,
                "logs": bool(execution_records),
                "stop": active_count > 0,
                "destroy": True,
            },
        )

    reconcile_container_execution_records = _reconcile_container_execution_records
    container_execution_payload = _container_execution_payload
    container_worker_image_details = _container_worker_image_details
    runtime_image_ref_display_details = _runtime_image_ref_display_details
    agent_record_from_container_execution = _agent_record_from_container_execution
    agent_record_from_container_session = _agent_record_from_container_session
