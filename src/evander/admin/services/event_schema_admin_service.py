from __future__ import annotations

from typing import Any

from evander.events.schema import EventDirectoryConfig, EventSchemaService, revision


class EventSchemaRevisionConflict(ValueError):
    pass


class EventSchemaAdminService:
    def __init__(
        self, schemas: EventSchemaService, directory_admin: Any, mutation_lock: Any
    ) -> None:
        self.schemas = schemas
        self.directory_admin = directory_admin
        self.mutation_lock = mutation_lock

    def snapshot(self, directory_path: str) -> dict[str, Any]:
        result = self.directory_admin.read_domain_config(directory_path, domain="events")
        if not result.ok or result.data is None:
            raise ValueError(
                result.errors[0].message if result.errors else "Unable to read event definitions"
            )
        raw = result.data.config or {}
        catalog = self.schemas.catalog(directory_path)
        inherited = (
            self.schemas.catalog(directory_path.rsplit("/", 1)[0])
            if directory_path != "root"
            else [entry.model_dump(mode="json") for entry in self.schemas.defaults()]
        )
        return {
            "directory_path": directory_path,
            "config": raw,
            "events": catalog,
            "inherited_events": inherited,
            "revision": revision([raw, catalog, inherited]),
        }

    def save(self, directory_path: str, *, expected_revision: str, config: Any) -> dict[str, Any]:
        normalized = EventDirectoryConfig.model_validate(config).model_dump(
            mode="json", exclude_unset=True
        )
        with self.mutation_lock():
            if self.snapshot(directory_path)["revision"] != expected_revision:
                raise EventSchemaRevisionConflict(
                    "Event definitions changed. Reload before saving again."
                )
            result = self.directory_admin.write_domain_config(
                directory_path, domain="events", payload=normalized
            )
            if not result.ok:
                raise ValueError(
                    result.errors[0].message
                    if result.errors
                    else "Unable to save event definitions"
                )
            return self.snapshot(directory_path)
