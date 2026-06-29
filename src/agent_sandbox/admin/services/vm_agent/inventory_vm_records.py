from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.common import (
    AgentRecord,
    Any,
    SessionManifest,
    VmSummary,
    _normalize_session_labels,
)


class VmAgentVmRecordInventoryMixin:
    def _agent_record_from_vm(
        self,
        summary: VmSummary,
        *,
        include_effective_tool_policy: bool = True,
    ) -> AgentRecord:
        power_state = str(summary.power_state or summary.libvirt_state or "unknown").strip()
        status = "running" if power_state == "running" else "stopped"
        directory_path = summary.directory_paths[0] if len(summary.directory_paths) == 1 else "root"
        session_policy = None
        effective_tool_policy = None
        runtime_image: dict[str, Any] | None = None
        if len(summary.session_ids) == 1:
            session = self.runtime.session_repository.get_session(summary.session_ids[0])
            if session is not None:
                session_policy = self._agent_session_policy_payload(session)
                if include_effective_tool_policy:
                    effective_tool_policy = self.effective_tool_policy_payload(session)
                runtime_image = self.session_runtime_image_payload(session)
        host: dict[str, Any] = {
            "kind": "vm",
            "vm_name": summary.vm_name,
            "provider": summary.provider,
            "issue_number": summary.issue_number,
            "session_ids": list(summary.session_ids),
            "labels": dict(summary.labels),
            "directory_paths": list(summary.directory_paths),
            "power_state": summary.power_state,
            "libvirt_state": summary.libvirt_state,
            "provider_state": summary.provider_state,
            "provider_details": dict(summary.provider_details),
            "ip_address": summary.ip_address,
            "agent": {
                "state": summary.agent_state,
                "pid": summary.agent_pid,
                "dispatch_id": summary.dispatch_id,
                "repo_names": list(summary.agent_repo_names),
                "preferred_repo_name": summary.agent_repo_names[0]
                if len(summary.agent_repo_names) == 1
                else None,
            },
            "session_policy": session_policy,
        }
        if runtime_image is not None:
            host["runtime_image"] = runtime_image
        return AgentRecord(
            agent_id=summary.vm_name,
            backend="libvirt_vm",
            target_name=summary.vm_name,
            session_id=summary.session_ids[0] if len(summary.session_ids) == 1 else None,
            directory_path=directory_path,
            status=status,
            updated_at=summary.last_state_update_at,
            effective_tool_policy=effective_tool_policy,
            host=host,
            capabilities={
                "start": True,
                "shutdown": True,
                "resume": True,
                "enable_disable": True,
                "kill": True,
                "logs": True,
                "stop": False,
                "destroy": True,
            },
        )

    def _agent_record_from_stale_vm_session(
        self,
        session: SessionManifest,
        *,
        inventory_error: str | None = None,
        include_effective_tool_policy: bool = True,
    ) -> AgentRecord:
        vm_name = str(session.vm_id or session.vm_provider_id or session.session_id).strip()
        directory_path = self._agent_record_directory_path(session.directory_path)
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        labels = _normalize_session_labels(metadata.get("labels"))
        host: dict[str, Any] = {
            "kind": "vm_session",
            "vm_name": vm_name,
            "provider": str(session.vm_provider or "libvirt").strip() or "libvirt",
            "session_ids": [session.session_id],
            "labels": labels,
            "directory_paths": [directory_path],
            "inventory_status": "missing",
            "session_policy": self._agent_session_policy_payload(session),
        }
        if inventory_error:
            host["inventory_error"] = inventory_error
        host["runtime_image"] = self.session_runtime_image_payload(session)
        return AgentRecord(
            agent_id=vm_name,
            backend="libvirt_vm",
            target_name=vm_name,
            session_id=session.session_id,
            directory_path=directory_path,
            status="stale",
            updated_at=session.created_at,
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
                "logs": False,
                "stop": False,
                "destroy": True,
            },
        )

    agent_record_from_vm = _agent_record_from_vm
    agent_record_from_stale_vm_session = _agent_record_from_stale_vm_session
