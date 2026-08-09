from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from evander.storage import JsonStore


LOCAL_INFERENCE_CAPACITY_STORE_FILENAME = "local-inference-capacity.json"
LOCAL_INFERENCE_QUEUE_REASON = "local_inference_capacity"
LOCAL_INFERENCE_LAUNCH_TIMEOUT_SECONDS = 300


class LocalInferenceCapacityLease(BaseModel):
    slot_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    session_id: str | None = None
    dispatch_id: str | None = None
    effect_id: str | None = None
    execution_backend: str | None = None
    model: str = Field(min_length=1)
    model_provider_mode: Literal["local_openai"] = "local_openai"
    status: Literal["launching", "running"] = "launching"
    claimed_at: datetime
    launched_at: datetime | None = None
    updated_at: datetime


class LocalInferenceCapacityAdmission(BaseModel):
    enabled: bool
    admitted: bool
    slot_id: str | None = None
    active_count: int = Field(default=0, ge=0)
    max_concurrency: int | None = Field(default=None, ge=1)

    def output_metadata(self) -> dict[str, object]:
        return {
            "local_inference_capacity_enabled": self.enabled,
            "local_inference_active_count": self.active_count,
            "local_inference_max_concurrency": self.max_concurrency,
            **(
                {"local_inference_slot_id": self.slot_id}
                if self.slot_id is not None
                else {}
            ),
        }


class LocalInferenceCapacityStore:
    """Cross-process host-wide admission ledger for complete local model runs."""

    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.store = JsonStore(
            self.runtime_dir / LOCAL_INFERENCE_CAPACITY_STORE_FILENAME,
            LocalInferenceCapacityLease,
        )

    def claim(
        self,
        *,
        max_concurrency: int | None,
        owner_id: str,
        model: str,
        session_id: str | None = None,
        dispatch_id: str | None = None,
        effect_id: str | None = None,
        execution_backend: str | None = None,
        now: datetime | None = None,
    ) -> LocalInferenceCapacityAdmission:
        if max_concurrency is None:
            return LocalInferenceCapacityAdmission(enabled=False, admitted=True)
        normalized_limit = max(1, int(max_concurrency))
        effective_now = now or datetime.now(UTC)
        launch_cutoff = effective_now - timedelta(
            seconds=LOCAL_INFERENCE_LAUNCH_TIMEOUT_SECONDS
        )

        def updater(
            records: dict[str, LocalInferenceCapacityLease],
        ) -> LocalInferenceCapacityAdmission:
            stale_slot_ids = [
                slot_id
                for slot_id, record in records.items()
                if record.status == "launching" and record.updated_at <= launch_cutoff
            ]
            for slot_id in stale_slot_ids:
                records.pop(slot_id, None)

            for record in records.values():
                if record.owner_id == owner_id:
                    return LocalInferenceCapacityAdmission(
                        enabled=True,
                        admitted=True,
                        slot_id=record.slot_id,
                        active_count=len(records),
                        max_concurrency=normalized_limit,
                    )
            active_count = len(records)
            if active_count >= normalized_limit:
                return LocalInferenceCapacityAdmission(
                    enabled=True,
                    admitted=False,
                    active_count=active_count,
                    max_concurrency=normalized_limit,
                )
            slot_id = f"local-inference-{uuid4().hex}"
            records[slot_id] = LocalInferenceCapacityLease(
                slot_id=slot_id,
                owner_id=owner_id,
                session_id=session_id,
                dispatch_id=dispatch_id,
                effect_id=effect_id,
                execution_backend=execution_backend,
                model=model,
                claimed_at=effective_now,
                updated_at=effective_now,
            )
            return LocalInferenceCapacityAdmission(
                enabled=True,
                admitted=True,
                slot_id=slot_id,
                active_count=active_count + 1,
                max_concurrency=normalized_limit,
            )

        return self.store.update_all(updater)

    def mark_running(
        self,
        slot_id: str | None,
        *,
        now: datetime | None = None,
    ) -> bool:
        normalized_slot_id = str(slot_id or "").strip()
        if not normalized_slot_id:
            return False
        effective_now = now or datetime.now(UTC)

        def updater(records: dict[str, LocalInferenceCapacityLease]) -> bool:
            record = records.get(normalized_slot_id)
            if record is None:
                return False
            record.status = "running"
            record.launched_at = record.launched_at or effective_now
            record.updated_at = effective_now
            return True

        return self.store.update_all(updater)

    def release(self, slot_id: str | None) -> bool:
        normalized_slot_id = str(slot_id or "").strip()
        if not normalized_slot_id:
            return False

        def updater(records: dict[str, LocalInferenceCapacityLease]) -> bool:
            return records.pop(normalized_slot_id, None) is not None

        return self.store.update_all(updater)

    def read_all(self) -> dict[str, LocalInferenceCapacityLease]:
        return self.store.read_all()


def local_inference_capacity_store(runtime_dir: str | Path) -> LocalInferenceCapacityStore:
    return LocalInferenceCapacityStore(runtime_dir)
