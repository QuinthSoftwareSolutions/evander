"""Session ownership and private peer transport for independent add-ons."""

from dataclasses import dataclass
from datetime import UTC, datetime
import os
import httpx
from evander.models import DirectoryPath
from evander.execution.worker_assignments import ContainerWorkerAssignmentRepository
from .models import ResourceError


@dataclass(frozen=True)
class SessionOwner:
    session_id: str
    directory: str
    expires_at: float
    worker_id: str | None
    worker_url: str | None


def session_owner(runtime_dir, service, session_id, tool=None):
    runtime = getattr(service, "runtime", None) or service
    if tool:
        runtime.policy.require_tool(session_id, tool)
    manifest = runtime.policy.get_session(session_id)
    if manifest.expires_at <= datetime.now(UTC):
        raise ResourceError("The owning session expired")
    assignment = ContainerWorkerAssignmentRepository(runtime_dir).get(session_id)
    return SessionOwner(
        session_id,
        DirectoryPath(segments=manifest.directory_path).as_posix(),
        manifest.expires_at.timestamp(),
        assignment.worker_id if assignment else None,
        assignment.worker_url.rstrip("/") if assignment else None,
    )


class Peer:
    def __init__(self, base_url, token_env, *, timeout=45):
        self.base_url, self.token_env = base_url.rstrip("/"), token_env
        self.timeout = timeout

    def call(self, method, path, payload=None):
        token = os.environ.get(self.token_env)
        if not token:
            raise ResourceError("Private worker credentials are unavailable")
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout, connect=min(5, self.timeout)),
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = client.request(
                    method,
                    self.base_url + path,
                    json=payload,
                    headers={"Authorization": "Bearer " + token},
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError):
            raise ResourceError("Worker result is unavailable; inspect before retrying") from None
