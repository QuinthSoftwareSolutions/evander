from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .directories import DirectoryService
from .models import DirectoryInfo, SessionManifest
from .storage import JsonStore


class OwnedEntity(BaseModel):
    kind: str
    entity_id: str
    directory_path: str
    label: str
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)


class DirectoryOwnershipSnapshot(BaseModel):
    directory: DirectoryInfo
    descendants: list[DirectoryInfo] = Field(default_factory=list)
    entities: list[OwnedEntity] = Field(default_factory=list)


class DirectoryOwnershipQueries:
    def __init__(
        self,
        *,
        directories: DirectoryService,
        sessions: JsonStore[SessionManifest],
        pr_map_path: Path | None = None,
        ado_processed_store_path: Path | None = None,
    ) -> None:
        self._directories = directories
        self._sessions = sessions
        self._pr_map_path = pr_map_path
        self._ado_processed_store_path = ado_processed_store_path

    def list_descendants(self, reference: str) -> list[DirectoryInfo]:
        return self._directories.list_descendants(reference)

    def list_entities(
        self,
        reference: str,
        *,
        include_descendants: bool = False,
    ) -> DirectoryOwnershipSnapshot:
        directory = self._directories.show(reference).directory
        descendants = self.list_descendants(directory.path)
        allowed_paths = {directory.path}
        if include_descendants:
            allowed_paths.update(item.path for item in descendants)

        sessions = self._sessions.read_all()
        entities: list[OwnedEntity] = []
        entities.extend(self._session_entities(sessions, allowed_paths=allowed_paths))
        entities.extend(self._pr_map_entities(sessions, allowed_paths=allowed_paths))
        entities.extend(self._ado_processed_entities(sessions, allowed_paths=allowed_paths))
        entities.sort(key=lambda item: (item.directory_path, item.kind, item.entity_id))
        return DirectoryOwnershipSnapshot(
            directory=directory,
            descendants=descendants,
            entities=entities,
        )

    def _session_entities(
        self,
        sessions: dict[str, SessionManifest],
        *,
        allowed_paths: set[str],
    ) -> list[OwnedEntity]:
        entities: list[OwnedEntity] = []
        for session_id, session in sorted(sessions.items()):
            if session.directory_path not in allowed_paths:
                continue
            entities.append(
                OwnedEntity(
                    kind="session",
                    entity_id=session_id,
                    directory_path=session.directory_path,
                    label=session_id,
                    source="session_manifest",
                    payload={
                        "session_id": session_id,
                        "vm_id": session.vm_id,
                    },
                )
            )
            if session.vm_id:
                entities.append(
                    OwnedEntity(
                        kind="vm",
                        entity_id=session.vm_id,
                        directory_path=session.directory_path,
                        label=session.vm_id,
                        source="session_manifest",
                        payload={
                            "session_id": session_id,
                            "vm_id": session.vm_id,
                        },
                    )
                )
            issue_number = session.metadata.get("issue_number")
            if issue_number is not None:
                entities.append(
                    OwnedEntity(
                        kind="issue",
                        entity_id=str(issue_number),
                        directory_path=session.directory_path,
                        label=str(issue_number),
                        source="session_manifest",
                        payload={
                            "session_id": session_id,
                            "vm_id": session.vm_id,
                        },
                    )
                )
        return entities

    def _pr_map_entities(
        self,
        sessions: dict[str, SessionManifest],
        *,
        allowed_paths: set[str],
    ) -> list[OwnedEntity]:
        if self._pr_map_path is None or not self._pr_map_path.exists():
            return []
        payload = self._read_json_file(self._pr_map_path)
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            return []
        entities: list[OwnedEntity] = []
        for entry_key, raw_entry in sorted(entries.items()):
            if not isinstance(raw_entry, dict):
                continue
            session_id = str(raw_entry.get("session_id", "")).strip()
            directory_path = self._owned_directory_path(raw_entry, sessions=sessions, session_id=session_id)
            if directory_path is None or directory_path not in allowed_paths:
                continue
            entities.append(
                OwnedEntity(
                    kind="pr_mapping",
                    entity_id=entry_key,
                    directory_path=directory_path,
                    label=entry_key,
                    source="pr_map",
                    payload={
                        "provider": raw_entry.get("provider"),
                        "repo": raw_entry.get("repo"),
                        "pr_id": raw_entry.get("pr_id"),
                        "session_id": session_id or None,
                    },
                )
            )
        return entities

    def _ado_processed_entities(
        self,
        sessions: dict[str, SessionManifest],
        *,
        allowed_paths: set[str],
    ) -> list[OwnedEntity]:
        if self._ado_processed_store_path is None or not self._ado_processed_store_path.exists():
            return []
        payload = self._read_json_file(self._ado_processed_store_path)
        entities: list[OwnedEntity] = []
        for entry_key, raw_entry in sorted(payload.items()):
            if not isinstance(raw_entry, dict):
                continue
            session_id = str(raw_entry.get("session_id", "")).strip()
            directory_path = self._owned_directory_path(raw_entry, sessions=sessions, session_id=session_id)
            if directory_path is None or directory_path not in allowed_paths:
                continue
            work_item_id = str(raw_entry.get("remote_work_item_id") or "").strip() or entry_key
            entities.append(
                OwnedEntity(
                    kind="ado_work_item",
                    entity_id=entry_key,
                    directory_path=directory_path,
                    label=work_item_id,
                    source="ado_processed_store",
                    payload={
                        "session_id": session_id or None,
                        "remote_work_item_id": work_item_id,
                        "yaml_path": raw_entry.get("yaml_path"),
                        "script_exit_code": raw_entry.get("script_exit_code"),
                    },
                )
            )
        return entities

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _owned_directory_path(
        raw_entry: dict[str, Any],
        *,
        sessions: dict[str, SessionManifest],
        session_id: str,
    ) -> str | None:
        directory_path = raw_entry.get("directory_path")
        if isinstance(directory_path, str) and directory_path.strip():
            return directory_path.strip()
        if session_id and session_id in sessions:
            return sessions[session_id].directory_path
        return None
