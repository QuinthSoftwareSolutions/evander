from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from evander.models import utc_now


class AgentControlRecord(BaseModel):
    vm_name: str
    status: Literal["idle", "disabled"]
    phase: str = ""
    updated_at: datetime = Field(default_factory=utc_now)


class AgentControlStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock_path = path.with_suffix(f"{path.suffix}.lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def get(self, vm_name: str) -> AgentControlRecord | None:
        return self.read_all().get(vm_name)

    def read_all(self) -> dict[str, AgentControlRecord]:
        payload = self._read_raw()
        return {key: AgentControlRecord.model_validate(value) for key, value in payload.items()}

    def status_for_vm(self, vm_name: str) -> str | None:
        record = self.get(vm_name)
        if record is None:
            return None
        return record.status

    def is_disabled(self, vm_name: str) -> bool:
        return self.status_for_vm(vm_name) == "disabled"

    def set_status(self, *, vm_name: str, status: Literal["idle", "disabled"], phase: str) -> None:
        with self._exclusive_lock():
            payload = self._read_raw()
            payload[vm_name] = AgentControlRecord(
                vm_name=vm_name,
                status=status,
                phase=phase,
            ).model_dump(mode="json")
            self._write_raw(payload)

    def _read_raw(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_raw(self, payload: dict[str, dict]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

    def _exclusive_lock(self):
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)

        class _LockHandle:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb) -> None:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

        return _LockHandle()
