from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from evander.models import SessionManifest
from evander.execution.worker_assignments import ContainerWorkerAssignmentRepository
from evander.admin.services.vm_agent.common_models import AgentCreateOperation


# Session policy is copied in full; execution identity and history are fresh.
SESSION_IDENTITY_FIELDS = frozenset(
    {
        "session_id",
        "vm_id",
        "vm_provider",
        "vm_provider_id",
        "created_at",
        "expires_at",
        "metadata",
    }
)
SESSION_CONFIGURATION_METADATA = frozenset(
    {
        "agent_provider",
        "agent_harness",
        "model_provider_mode",
        "labels",
        "http_targets",
        "app",
        "codex_skills",
        "dotnet",
        "nuget",
        "playwright",
        "mssql",
        "mssql_init_root",
        "vm_user",
        "capability_profile",
        "assistant_read_only",
        "assistant_mutation_policy",
    }
)


def session_copy_configuration(source: SessionManifest) -> dict[str, Any]:
    configuration = source.model_dump(mode="json", exclude=SESSION_IDENTITY_FIELDS)
    configuration["metadata"] = {
        key: deepcopy(value)
        for key, value in source.metadata.items()
        if key in SESSION_CONFIGURATION_METADATA
    }
    return configuration


def session_copy_create_payload(source: SessionManifest, *, session_id: str) -> dict[str, Any]:
    configuration = session_copy_configuration(source)
    metadata = configuration.pop("metadata")
    backend = source.metadata.get("execution_backend") or (
        "container_worker_vm" if source.vm_provider == "container_worker_vm" else "libvirt_vm"
    )
    payload = {
        **configuration,
        **metadata,
        "backend": backend,
        "session_id": session_id,
        "ttl_seconds": max(1, int((source.expires_at - source.created_at).total_seconds())),
        "discord_channels": source.discord_channel_allowlist,
        "email_mailboxes": source.email_mailbox_allowlist,
        "email_folders": source.email_folder_allowlist,
    }
    if metadata.get("agent_harness") and not metadata.get("agent_provider"):
        payload["agent_provider"] = metadata["agent_harness"]
    if backend == "container_worker_vm":
        payload["target_name"] = session_id
        image = source.metadata.get("container_worker") or {}
        payload["container_image"] = image.get("image") or image.get("image_alias")
    else:
        payload["vm_name"] = f"vm-copy-{uuid4().hex}"
        payload["provider"] = source.vm_provider
        template = source.metadata.get("libvirt_vm_template") or {}
        payload["vm_image"] = template.get("image_ref") or template.get("template_alias")
    return payload


class VmAgentCopyMixin:
    def submit_admin_agent_copy(
        self, source: SessionManifest, *, session_id: str | None = None
    ) -> AgentCreateOperation:
        if session_id is not None and (
            not isinstance(session_id, str) or not session_id.strip()
        ):
            raise ValueError("session_id must be a non-empty string when supplied")
        destination = (session_id or f"session-copy-{uuid4().hex}").strip()
        if self.runtime.session_repository.get_session(destination) is not None:
            raise ValueError(f"Destination session already exists: {destination}")
        payload = session_copy_create_payload(source, session_id=destination)
        if payload["backend"] == "container_worker_vm":
            assignment = ContainerWorkerAssignmentRepository(self.config.runtime_dir).get(
                source.session_id
            )
            if assignment is not None and assignment.worker_id:
                payload["worker_id"] = assignment.worker_id
        return self._submit_admin_agent_create(
            payload,
            copy_configuration=session_copy_configuration(source),
        )

    def _finish_admin_agent_copy(self, session_id: str, configuration: dict[str, Any]) -> None:
        repository = self.runtime.session_repository
        created = repository.get_session(session_id)
        if created is None:
            raise ValueError(f"Created session was not found: {session_id}")
        policy = deepcopy(configuration)
        policy["metadata"] = {**created.metadata, **policy["metadata"]}
        copied = SessionManifest.model_validate({**created.model_dump(), **policy})
        repository.upsert_session(copied)
        self.sync_added_discord_session_cursors(previous_session=None, updated_session=copied)
