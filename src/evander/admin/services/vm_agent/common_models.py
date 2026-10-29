from __future__ import annotations

from evander.admin.services.vm_agent.common_imports import (
    dataclass,
    AGENT_ARTIFACTS_ENV,
    Any,
    DEFAULT_AGENT_ARTIFACTS_DIR,
    re,
)

from evander.admin.services.agent_create_operation_service import (
    AgentCreateOperation,
    AgentCreateOperationStatus,
)


_ASSISTANT_AGENTS_DESTINATION_PATH = "/home/agent/AGENTS.md"
_ASSISTANT_AGENTS_TEMPLATE_NAME = "chat-assistant-AGENTS.md"
AGENT_CREATE_OPERATION_MAX_WORKERS = 4
_CONTAINER_SESSION_ID_LOG_PATTERN = re.compile(
    r"^session id:\s*(?P<thread_id>\S+)\s*$", re.IGNORECASE | re.MULTILINE
)
_VM_ARTIFACT_TEXT_READ_COMMAND = f"""python3 - <<'PY'
from pathlib import Path, PurePosixPath
import os
import sys

root = Path(os.environ.get({AGENT_ARTIFACTS_ENV!r}) or {DEFAULT_AGENT_ARTIFACTS_DIR!r})
relative_text = os.environ.get("EVANDER_ARTIFACT_RELATIVE_PATH") or ""
relative = PurePosixPath(relative_text)
if not relative_text or relative.is_absolute() or any(part in {{"", ".", ".."}} for part in relative.parts):
    print("artifact path must be relative and must not contain dot or parent segments", file=sys.stderr)
    raise SystemExit(64)
try:
    root_resolved = root.resolve(strict=True)
    target = (root / relative.as_posix()).resolve(strict=True)
    target.relative_to(root_resolved)
except FileNotFoundError:
    print(f"artifact file not found: {{relative.as_posix()}}", file=sys.stderr)
    raise SystemExit(66)
except ValueError:
    print("artifact path must stay within the session artifacts folder", file=sys.stderr)
    raise SystemExit(64)
if not target.is_file():
    print(f"artifact path is not a regular file: {{relative.as_posix()}}", file=sys.stderr)
    raise SystemExit(66)
try:
    sys.stdout.write(target.read_text(encoding="utf-8"))
except UnicodeDecodeError:
    print("artifact file is not valid UTF-8 text", file=sys.stderr)
    raise SystemExit(65)
PY
"""


@dataclass(frozen=True)
class NormalizedVmCreationOptions:
    provider: str | None
    allowed_contact_ids: list[str] | None
    payload: dict[str, Any]

    def provider_kwargs(self) -> dict[str, Any]:
        payload = dict(self.payload)
        payload["allowed_contact_ids"] = self.allowed_contact_ids
        return payload


__all__ = [
    "AGENT_CREATE_OPERATION_MAX_WORKERS",
    "AgentCreateOperation",
    "AgentCreateOperationStatus",
    "NormalizedVmCreationOptions",
    "_ASSISTANT_AGENTS_DESTINATION_PATH",
    "_ASSISTANT_AGENTS_TEMPLATE_NAME",
    "_CONTAINER_SESSION_ID_LOG_PATTERN",
    "_VM_ARTIFACT_TEXT_READ_COMMAND",
]
