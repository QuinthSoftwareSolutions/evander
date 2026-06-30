from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_sandbox.admin.payloads import (
    WorkflowPackActorPayload,
    WorkflowPackHistoryDetailPayload,
    WorkflowPackHistoryDiffPayload,
    WorkflowPackHistoryEntryPayload,
    WorkflowPackHistoryFilePayload,
    WorkflowPackHistoryListPayload,
)

_MISSING = object()


@dataclass(frozen=True, slots=True)
class WorkflowPackChangeActor:
    login: str = "system"
    display_name: str | None = None

    @classmethod
    def from_values(
        cls, *, login: object | None = None, display_name: object | None = None
    ) -> "WorkflowPackChangeActor":
        normalized_login = str(login or "").strip().lower() or "system"
        normalized_display_name = str(display_name or "").strip() or None
        return cls(login=normalized_login, display_name=normalized_display_name)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"login": self.login}
        if self.display_name:
            payload["display_name"] = self.display_name
        return payload


@dataclass(frozen=True, slots=True)
class WorkflowPackHistoryFileState:
    label: str
    path: Path
    exists: bool
    content: dict[str, object] | None
    revision: str | None
    error: str | None = None

    def to_payload(self, *, include_content: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "label": self.label,
            "path": str(self.path),
            "exists": self.exists,
            "revision": self.revision,
            "error": self.error,
        }
        if include_content:
            payload["content"] = self.content
        return payload


class WorkflowPackHistoryService:
    def __init__(
        self,
        *,
        runtime_dir: Path,
        stable_json_revision: Callable[[object], str],
    ) -> None:
        self.root_dir = runtime_dir / "workflow-pack-history"
        self.ledger_path = self.root_dir / "changes.jsonl"
        self.snapshots_dir = self.root_dir / "snapshots"
        self._stable_json_revision = stable_json_revision

    def capture_file_state(
        self, label: str, path: Path, *, tolerate_invalid: bool = False
    ) -> WorkflowPackHistoryFileState:
        if not path.exists():
            return WorkflowPackHistoryFileState(
                label=label,
                path=path,
                exists=False,
                content=None,
                revision=None,
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"{label} file {path} must contain a JSON object")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            if not tolerate_invalid:
                raise
            return WorkflowPackHistoryFileState(
                label=label,
                path=path,
                exists=True,
                content=None,
                revision=None,
                error=str(exc),
            )
        return WorkflowPackHistoryFileState(
            label=label,
            path=path,
            exists=True,
            content=payload,
            revision=self._stable_json_revision(payload),
        )

    def record_change(
        self,
        *,
        operation: str,
        workflow_pack: str,
        actor: WorkflowPackChangeActor | None,
        before_files: list[WorkflowPackHistoryFileState],
        after_files: list[WorkflowPackHistoryFileState],
        before_revision: str | None,
        after_revision: str | None,
    ) -> WorkflowPackHistoryEntryPayload:
        change_id = str(uuid4())
        normalized_actor = actor or WorkflowPackChangeActor()
        changed_at = datetime.now(UTC)
        touched_paths = sorted(
            {
                str(file_state.path)
                for file_state in [*before_files, *after_files]
                if self._file_state_changed(file_state, before_files, after_files)
            }
        )
        if not touched_paths:
            touched_paths = sorted({str(file_state.path) for file_state in after_files})
        summary = self._summary(before_files=before_files, after_files=after_files)
        record = {
            "change_id": change_id,
            "operation": operation,
            "workflow_pack": workflow_pack,
            "actor": normalized_actor.to_payload(),
            "changed_at": changed_at.isoformat().replace("+00:00", "Z"),
            "before_revision": before_revision,
            "after_revision": after_revision,
            "touched_paths": touched_paths,
            "summary": summary,
            "snapshot_dir": f"snapshots/{change_id}",
        }
        self._write_snapshots(change_id, before_files=before_files, after_files=after_files)
        self._append_record(record)
        return self._entry_payload(record)

    def list_history(
        self,
        *,
        workflow_pack: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> WorkflowPackHistoryListPayload:
        normalized_pack = str(workflow_pack or "").strip()
        entries = [
            record
            for record in self._read_records()
            if not normalized_pack or record.get("workflow_pack") == normalized_pack
        ]
        entries.reverse()
        offset = self._cursor_offset(cursor)
        bounded_limit = min(max(int(limit), 1), 200)
        page = entries[offset : offset + bounded_limit]
        next_offset = offset + bounded_limit
        next_cursor = str(next_offset) if next_offset < len(entries) else None
        return WorkflowPackHistoryListPayload(
            entries=[self._entry_payload(record) for record in page],
            next_cursor=next_cursor,
        )

    def get_history_detail(self, change_id: str) -> WorkflowPackHistoryDetailPayload:
        normalized_change_id = str(change_id or "").strip()
        if not normalized_change_id:
            raise ValueError("change_id is required")
        for record in self._read_records():
            if record.get("change_id") != normalized_change_id:
                continue
            before_files = self._read_snapshot_files(normalized_change_id, "before")
            after_files = self._read_snapshot_files(normalized_change_id, "after")
            return WorkflowPackHistoryDetailPayload(
                entry=self._entry_payload(record),
                before_files=[WorkflowPackHistoryFilePayload.model_validate(item) for item in before_files],
                after_files=[WorkflowPackHistoryFilePayload.model_validate(item) for item in after_files],
                diffs=self._diffs(before_files=before_files, after_files=after_files),
            )
        raise ValueError(f"Unknown workflow pack history change: {normalized_change_id}")

    def _append_record(self, record: dict[str, object]) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _write_snapshots(
        self,
        change_id: str,
        *,
        before_files: list[WorkflowPackHistoryFileState],
        after_files: list[WorkflowPackHistoryFileState],
    ) -> None:
        for stage, files in (("before", before_files), ("after", after_files)):
            stage_dir = self.snapshots_dir / change_id / stage
            stage_dir.mkdir(parents=True, exist_ok=True)
            for index, file_state in enumerate(files):
                snapshot_path = stage_dir / f"{index:02d}-{self._safe_label(file_state.label)}.json"
                self._write_json_file_atomic(snapshot_path, file_state.to_payload())

    def _write_json_file_atomic(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(encoded)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        try:
            os.replace(temp_path, path)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _read_records(self) -> list[dict[str, object]]:
        if not self.ledger_path.exists():
            return []
        records: list[dict[str, object]] = []
        for raw_line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def _read_snapshot_files(self, change_id: str, stage: str) -> list[dict[str, object]]:
        stage_dir = self.snapshots_dir / change_id / stage
        if not stage_dir.exists():
            return []
        result: list[dict[str, object]] = []
        for path in sorted(stage_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                result.append(payload)
        return result

    def _entry_payload(self, record: dict[str, object]) -> WorkflowPackHistoryEntryPayload:
        payload = dict(record)
        actor = payload.get("actor")
        if not isinstance(actor, dict):
            actor = {"login": "system"}
        payload["actor"] = WorkflowPackActorPayload.model_validate(actor)
        return WorkflowPackHistoryEntryPayload.model_validate(payload)

    def _summary(
        self,
        *,
        before_files: list[WorkflowPackHistoryFileState],
        after_files: list[WorkflowPackHistoryFileState],
    ) -> dict[str, object]:
        before_pack = self._content_for_label(before_files, "pack")
        after_pack = self._content_for_label(after_files, "pack")
        before_config = self._content_for_label(before_files, "worker_config")
        after_config = self._content_for_label(after_files, "worker_config")
        before_counts = self._pack_counts(before_pack)
        after_counts = self._pack_counts(after_pack)
        return {
            "nodes_before": before_counts["nodes"],
            "nodes_after": after_counts["nodes"],
            "edges_before": before_counts["edges"],
            "edges_after": after_counts["edges"],
            "graphs_before": before_counts["graphs"],
            "graphs_after": after_counts["graphs"],
            "workflow_packs_before": self._workflow_pack_count(before_config),
            "workflow_packs_after": self._workflow_pack_count(after_config),
        }

    @staticmethod
    def _content_for_label(
        files: list[WorkflowPackHistoryFileState], label: str
    ) -> dict[str, object] | None:
        for file_state in files:
            if file_state.label == label:
                return file_state.content
        return None

    @staticmethod
    def _workflow_pack_count(content: dict[str, object] | None) -> int:
        workflow_packs = content.get("workflow_packs") if isinstance(content, dict) else None
        return len(workflow_packs) if isinstance(workflow_packs, list) else 0

    @staticmethod
    def _pack_counts(content: dict[str, object] | None) -> dict[str, int]:
        if not isinstance(content, dict):
            return {"nodes": 0, "edges": 0, "graphs": 0}
        ir = content.get("ir")
        nodes = ir.get("nodes") if isinstance(ir, dict) else None
        edges = ir.get("edges") if isinstance(ir, dict) else None
        graphs = content.get("graphs")
        return {
            "nodes": len(nodes) if isinstance(nodes, list) else 0,
            "edges": len(edges) if isinstance(edges, list) else 0,
            "graphs": len(graphs) if isinstance(graphs, list) else 0,
        }

    @staticmethod
    def _safe_label(value: str) -> str:
        safe = "".join(character if character.isalnum() else "-" for character in value.lower())
        return safe.strip("-") or "file"

    @staticmethod
    def _cursor_offset(cursor: str | None) -> int:
        if cursor is None or not str(cursor).strip():
            return 0
        try:
            return max(int(str(cursor).strip()), 0)
        except ValueError:
            raise ValueError("cursor must be an integer offset") from None

    @staticmethod
    def _file_state_changed(
        file_state: WorkflowPackHistoryFileState,
        before_files: list[WorkflowPackHistoryFileState],
        after_files: list[WorkflowPackHistoryFileState],
    ) -> bool:
        before = next(
            (candidate for candidate in before_files if candidate.label == file_state.label),
            None,
        )
        after = next(
            (candidate for candidate in after_files if candidate.label == file_state.label),
            None,
        )
        if before is None or after is None:
            return True
        return before.exists != after.exists or before.revision != after.revision

    def _diffs(
        self,
        *,
        before_files: list[dict[str, object]],
        after_files: list[dict[str, object]],
    ) -> list[WorkflowPackHistoryDiffPayload]:
        after_by_label = {str(item.get("label") or ""): item for item in after_files}
        before_by_label = {str(item.get("label") or ""): item for item in before_files}
        labels = sorted({*before_by_label, *after_by_label})
        diffs: list[WorkflowPackHistoryDiffPayload] = []
        for label in labels:
            before = before_by_label.get(label)
            after = after_by_label.get(label)
            before_content = before.get("content") if isinstance(before, dict) else None
            after_content = after.get("content") if isinstance(after, dict) else None
            changes: list[dict[str, object]] = []
            truncated = self._append_json_diff(
                changes,
                "$",
                before_content if before_content is not None else _MISSING,
                after_content if after_content is not None else _MISSING,
                max_entries=200,
            )
            path = str((after or before or {}).get("path") or "")
            diffs.append(
                WorkflowPackHistoryDiffPayload(
                    label=label,
                    path=path,
                    changes=changes,
                    truncated=truncated,
                )
            )
        return diffs

    def _append_json_diff(
        self,
        changes: list[dict[str, object]],
        path: str,
        before: object,
        after: object,
        *,
        max_entries: int,
    ) -> bool:
        if len(changes) >= max_entries:
            return True
        if before is _MISSING and after is _MISSING:
            return False
        if before is _MISSING:
            changes.append({"path": path, "change_type": "added", "after": after})
            return len(changes) >= max_entries
        if after is _MISSING:
            changes.append({"path": path, "change_type": "removed", "before": before})
            return len(changes) >= max_entries
        if isinstance(before, dict) and isinstance(after, dict):
            truncated = False
            for key in sorted({*before.keys(), *after.keys()}, key=str):
                if len(changes) >= max_entries:
                    return True
                truncated = (
                    self._append_json_diff(
                        changes,
                        f"{path}.{key}",
                        before.get(key, _MISSING),
                        after.get(key, _MISSING),
                        max_entries=max_entries,
                    )
                    or truncated
                )
            return truncated
        if isinstance(before, list) and isinstance(after, list):
            truncated = False
            for index in range(max(len(before), len(after))):
                if len(changes) >= max_entries:
                    return True
                truncated = (
                    self._append_json_diff(
                        changes,
                        f"{path}[{index}]",
                        before[index] if index < len(before) else _MISSING,
                        after[index] if index < len(after) else _MISSING,
                        max_entries=max_entries,
                    )
                    or truncated
                )
            return truncated
        if before != after:
            changes.append(
                {
                    "path": path,
                    "change_type": "changed",
                    "before": before,
                    "after": after,
                }
            )
        return len(changes) >= max_entries
