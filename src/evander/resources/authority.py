"""Ownership extension. No imports of any optional consumer or resource provider."""

from __future__ import annotations

from typing import Protocol
import time

from evander.models import DirectoryPath

from .models import Owner, ResourceError


class ResourceAuthority(Protocol):
    def handles_session(self, session_id: str) -> bool: ...
    def resolve(self, session_id: str, reference: dict, tool: str) -> Owner: ...
    def owns(self, owner: Owner) -> bool: ...
    def notify(self, owner: Owner, receipt: dict) -> None: ...


def runtime_of(service):
    return getattr(service, "runtime", None) or service


class Authorities:
    def __init__(self, runtime, acquisition_tool=None, runtime_dir=None):
        self.runtime = runtime_of(runtime)
        self.acquisition_tool = acquisition_tool
        self.runtime_dir = runtime_dir

    def directory(self, session_id):
        session = self.runtime.policy.get_session(session_id)
        path = session.directory_path
        return (
            path.as_posix()
            if hasattr(path, "as_posix")
            else DirectoryPath(segments=path or "root").as_posix()
        )

    def resolve(self, session_id, provider, reference, tool):
        self.runtime.policy.require_tool(session_id, tool)
        providers = getattr(self.runtime, "resource_authorities", {})
        directory = self.directory(session_id)
        if provider == "session":
            if reference or any(p.handles_session(session_id) for p in providers.values()):
                raise ResourceError("This session requires its owning add-on's resource scope")
            owner = Owner(
                key=session_id,
                session_id=session_id,
                directory=directory,
                valid_until=time.time() + 120,
            )
        else:
            authority = providers.get(provider)
            if authority is None:
                raise ResourceError("Resource owner provider is not enabled")
            owner = authority.resolve(session_id, reference, tool)
        if (
            owner.session_id != session_id
            or owner.directory != directory
            or owner.provider != provider
        ):
            raise ResourceError("Resource authority returned a different session or directory")
        return owner

    def owns(self, owner):
        if owner.session_id and self.acquisition_tool:
            try:
                self.runtime.policy.require_tool(owner.session_id, self.acquisition_tool)
            except Exception:
                return False
        if owner.provider == "workflow":
            return owner.valid_until is not None and time.time() < owner.valid_until
        if owner.provider == "session":
            try:
                return self.directory(owner.session_id) == owner.directory
            except Exception:
                return False
        authority = getattr(self.runtime, "resource_authorities", {}).get(owner.provider)
        return bool(authority and authority.owns(owner))

    def notify(self, owner, value):
        authority = getattr(self.runtime, "resource_authorities", {}).get(owner.provider)
        if authority:
            authority.notify(owner, value)
        elif self.runtime_dir is not None:
            from evander.orchestration.event_publisher import InternalEventPublisherRuntime

            InternalEventPublisherRuntime(runtime_dir=self.runtime_dir).publish(
                event_type="resource:lease-state",
                payload={"owner": owner.model_dump(), "lease": value},
                correlation_key=owner.key,
                idempotency_key=value["notice_id"],
                source={"source_type": "resource-addon"},
            )
