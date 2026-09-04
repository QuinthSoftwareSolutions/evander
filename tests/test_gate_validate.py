from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from config_helpers import (
    canonical_worker_config,
    effect_execution_payload,
    gate_execution_payload,
)

from evander.config import GatewayConfig
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.effects.providers.base import EffectProviderBase
from evander.events.models import TriggerEvent


class _ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        future: Future[object] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future


class _RecordingEffectProvider(EffectProviderBase):
    def __init__(self, effect_id: str, calls: list[str]) -> None:
        super().__init__()
        self._effect_id = effect_id
        self._calls = calls

    def execute(
        self,
        *,
        resolved_config: dict[str, object] | None = None,
        execution_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del resolved_config, execution_context
        self._calls.append(self._effect_id)
        return {"effect_id": self._effect_id}


def _worker(tmp_path: Path) -> tuple[EffectOrchestratorWorker, list[str]]:
    runtime_dir = tmp_path / "runtime"
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Candidate",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {
                        "description": {"type": "string"},
                        "commit_id": {"type": "string"},
                        "work_item_count": {"type": "integer"},
                    },
                }
            ],
            "gates": [
                {
                    "id": "gate-validate",
                    "display_name": "Validate release package",
                    "type": "gate:validate",
                    "checks": [
                        {
                            "code": "description.checklist_prefix",
                            "message": "Description must start with the checklist separator.",
                            "when": {
                                "left": "${{trigger-1.description}}",
                                "matches_regex": "(?s)^---\\n\\n.*",
                            },
                        },
                        {
                            "code": "work_items.present",
                            "message": "At least one work item must be included.",
                            "when": {
                                "left": "${{trigger-1.work_item_count}}",
                                "greater_than": 0,
                            },
                        },
                        {
                            "code": "commit.matches",
                            "message": "The package must name the promoted commit.",
                            "when": {
                                "left": "${{trigger-1.commit_id}}",
                                "equals": "expected-commit",
                            },
                        },
                    ],
                }
            ],
            "effects": [
                {
                    "id": "effect-valid",
                    "display_name": "Publish",
                    "type": "effect:agent:prompt",
                    "prompt": "publish ${{gate-validate.failure_count}}",
                },
                {
                    "id": "effect-invalid",
                    "display_name": "Correct",
                    "type": "effect:agent:prompt",
                    "prompt": "correct ${{gate-validate.failure_count}}",
                },
                {
                    "id": "effect-error",
                    "display_name": "Repair validator",
                    "type": "effect:agent:prompt",
                    "prompt": "repair ${{gate-validate.error_message}}",
                },
            ],
            "edges": [
                {"source_id": "trigger-1", "target_id": "gate-validate"},
                {
                    "source_id": "gate-validate",
                    "source_port": "valid",
                    "target_id": "effect-valid",
                },
                {
                    "source_id": "gate-validate",
                    "source_port": "invalid",
                    "target_id": "effect-invalid",
                },
                {
                    "source_id": "gate-validate",
                    "source_port": "error",
                    "target_id": "effect-error",
                },
            ],
        }
    )
    worker = EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=runtime_dir),
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    calls: list[str] = []

    class _ProviderFactory:
        registry = SimpleNamespace(
            resolve=lambda effect_type: _RecordingEffectProvider,
            registered_types=lambda: ("effect:agent:prompt",),
        )

        def build(self, effect):
            return _RecordingEffectProvider(effect.id, calls)

    worker.provider_factory = _ProviderFactory()
    return worker, calls


def _publish(worker: EffectOrchestratorWorker, payload: dict[str, object]) -> None:
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
            trace_id="trace-1",
            payload=payload,
        )
    )
    for _ in range(5):
        worker.run_once()


def test_validate_gate_routes_valid_and_adds_empty_failure_outputs(tmp_path: Path) -> None:
    worker, calls = _worker(tmp_path)

    _publish(
        worker,
        {
            "description": "---\n\nThank you for your contribution.",
            "commit_id": "expected-commit",
            "work_item_count": 2,
        },
    )

    record = next(iter(gate_execution_payload(worker.worker_config).values()))
    assert record["status"] == "succeeded"
    assert record["completion_event_branch_port"] == "valid"
    assert record["outputs"] == {
        "description": "---\n\nThank you for your contribution.",
        "commit_id": "expected-commit",
        "work_item_count": 2,
        "valid": True,
        "failure_count": 0,
        "failed_codes": [],
        "failures": [],
    }
    assert [check["passed"] for check in record["resolved_config"]["checks"]] == [
        True,
        True,
        True,
    ]
    assert calls == ["effect-valid"]
    effect = next(iter(effect_execution_payload(worker.worker_config).values()))
    assert effect["resolved_config"]["prompt"] == "publish 0"


def test_validate_gate_collects_all_failures_in_order_and_routes_invalid(
    tmp_path: Path,
) -> None:
    worker, calls = _worker(tmp_path)

    _publish(
        worker,
        {
            "description": "---\nThank you for your contribution.",
            "commit_id": "wrong-commit",
            "work_item_count": 2,
            "valid": True,
            "__for_execution_ids": {"old-loop": "old-execution"},
        },
    )

    record = next(iter(gate_execution_payload(worker.worker_config).values()))
    expected_failures = [
        {
            "code": "description.checklist_prefix",
            "message": "Description must start with the checklist separator.",
        },
        {
            "code": "commit.matches",
            "message": "The package must name the promoted commit.",
        },
    ]
    assert record["status"] == "succeeded"
    assert record["completion_event_branch_port"] == "invalid"
    assert record["outputs"]["valid"] is False
    assert record["outputs"]["failure_count"] == 2
    assert record["outputs"]["failed_codes"] == [
        "description.checklist_prefix",
        "commit.matches",
    ]
    assert record["outputs"]["failures"] == expected_failures
    assert "__for_execution_ids" not in record["outputs"]
    assert [check["passed"] for check in record["resolved_config"]["checks"]] == [
        False,
        True,
        False,
    ]
    assert {entry["config_path"] for entry in record["binding_trace"]["entries"]} == {
        "$.checks[0].when.left",
        "$.checks[1].when.left",
        "$.checks[2].when.left",
    }
    assert calls == ["effect-invalid"]
    effect = next(iter(effect_execution_payload(worker.worker_config).values()))
    assert effect["resolved_config"]["prompt"] == "correct 2"


def test_validate_gate_routes_configuration_or_evaluation_failures_to_error(
    tmp_path: Path,
) -> None:
    worker, calls = _worker(tmp_path)

    _publish(
        worker,
        {
            "description": "---\n\nThank you for your contribution.",
            "commit_id": "expected-commit",
        },
    )

    record = next(iter(gate_execution_payload(worker.worker_config).values()))
    assert record["status"] == "error_routed"
    assert record["completion_event_branch_port"] == "error"
    assert "work_item_count" in record["error"]
    assert calls == ["effect-error"]
    effect = next(iter(effect_execution_payload(worker.worker_config).values()))
    assert effect["resolved_config"]["prompt"].startswith("repair ")
    assert "work_item_count" in effect["resolved_config"]["prompt"]
