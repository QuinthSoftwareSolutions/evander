"""Built-in contracts for bodies emitted on the durable internal event bus."""

from evander.workflow.catalog import WorkflowInternalEventCatalogEntry


def _entry(name, label, properties, required):
    return WorkflowInternalEventCatalogEntry(
        event_type=name,
        label=label,
        source="Platform",
        payload_schema={
            "type": "object",
            "properties": properties,
            "required": required,
        },
    )


_TEXT = {"type": "string"}
BUILTIN_EVENT_CATALOG = (
    _entry(
        "agent.consultation.requested",
        "Code consultation requested",
        {
            "consultation_id": _TEXT,
            "directory_path": _TEXT,
        },
        ["consultation_id", "directory_path"],
    ),
    _entry(
        "agent.consultation.terminate",
        "Code consultation termination",
        {
            "consultation_id": _TEXT,
            "reason": _TEXT,
            "status": _TEXT,
        },
        ["consultation_id", "reason", "status"],
    ),
    _entry(
        "agent.artifact.staged",
        "Agent artifact staged",
        {
            "session_id": _TEXT,
            "artifact_path": _TEXT,
            "source_path": _TEXT,
            "artifacts_root": _TEXT,
            "copied_count": {"type": "integer", "minimum": 0},
            "byte_count": {"type": "integer", "minimum": 0},
            "vm_name": {"type": ["string", "null"]},
            "execution_backend": {"type": ["string", "null"]},
            "directory_path": _TEXT,
        },
        [
            "session_id",
            "artifact_path",
            "source_path",
            "artifacts_root",
            "copied_count",
            "byte_count",
            "vm_name",
            "execution_backend",
            "directory_path",
        ],
    ),
)
