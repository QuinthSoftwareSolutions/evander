from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.effects.providers.base import EffectLifecycleHookBase, EffectProviderBase
from evander.effects.providers.lifecycle_hooks import (
    ProviderCompletionLifecycleHook,
    build_effect_lifecycle_hooks,
)
from evander.orchestration.maintenance import MaintenanceResult, RecoverySchedule


def _recover(monkeypatch, result: MaintenanceResult, *, slow: bool = False) -> None:
    worker = EffectOrchestratorWorker.__new__(EffectOrchestratorWorker)
    now = [0.0]
    worker._recovery_schedule = RecoverySchedule(clock=lambda: now[0])
    now[0] = 6.0
    worker._lifecycle_reconciliation_hooks = (
        SimpleNamespace(
            maintenance_name="example-recovery",
            run_maintenance_slice=lambda **kwargs: result,
        ),
    )
    worker._completion_dispatch_hooks = ()
    ticks = iter((0.0, 1.0, 1.0)) if slow else iter((0.0, 0.0, 0.0))
    monkeypatch.setattr(
        "evander.daemons.effect_orchestrator.time.perf_counter", lambda: next(ticks)
    )
    worker._run_due_recovery(graph=None, foreground_pending=True)


def test_idle_recovery_keeps_context_at_debug_without_info(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="evander.daemons.effect_orchestrator")
    _recover(monkeypatch, MaintenanceResult(examined=0))
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.DEBUG
    assert "example-recovery" in caplog.text
    assert "foreground_pending" in caplog.text


@pytest.mark.parametrize(
    ("result", "slow"),
    [
        (MaintenanceResult(examined=1), False),
        (MaintenanceResult(changes=1), False),
        (MaintenanceResult(remaining=True), False),
        (MaintenanceResult(examined=0), True),
    ],
)
def test_active_or_slow_recovery_remains_visible_at_info(monkeypatch, caplog, result, slow):
    caplog.set_level(logging.INFO, logger="evander.daemons.effect_orchestrator")
    _recover(monkeypatch, result, slow=slow)
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.INFO
    assert "example-recovery" in caplog.text


def test_completion_factory_skips_empty_callbacks_and_retains_inherited_and_custom_hooks():
    calls = []

    class CustomHook(EffectLifecycleHookBase):
        def on_post_completion_dispatch(self, *, worker):
            calls.append("custom")
            return 1

    class OrdinaryProvider(EffectProviderBase):
        @classmethod
        def lifecycle_hooks(cls):
            return (CustomHook,)

    class CompletionProvider(EffectProviderBase):
        @classmethod
        def on_completion_event(cls, **kwargs):
            calls.append("completion")
            return 1

    class InheritedCompletionProvider(CompletionProvider):
        pass

    class WaitingProvider(EffectProviderBase):
        @classmethod
        def reconcile_waiting(cls, **kwargs):
            calls.append("waiting")
            return 1

    class IndependentProvider:
        @classmethod
        def lifecycle_hooks(cls):
            return (CustomHook,)

    classes = [OrdinaryProvider, InheritedCompletionProvider, WaitingProvider, IndependentProvider]
    factory = SimpleNamespace(
        registry=SimpleNamespace(
            registered_types=lambda: range(len(classes)), resolve=classes.__getitem__
        )
    )
    hooks = build_effect_lifecycle_hooks(provider_factory=factory)
    assert len(hooks) == 4
    assert isinstance(hooks[0], CustomHook)
    wrappers = [hook for hook in hooks if isinstance(hook, ProviderCompletionLifecycleHook)]
    assert [hook._provider_class for hook in wrappers] == [
        InheritedCompletionProvider,
        WaitingProvider,
    ]
    assert hooks[0].on_post_completion_dispatch(worker=None) == 1
    assert wrappers[0].on_completion_fan_out(worker=None, queue_record=None, graph=None) == 1
    assert wrappers[1].on_waiting_reconciliation(worker=None) == 1
    assert isinstance(hooks[3], CustomHook)
    assert hooks[3].on_post_completion_dispatch(worker=None) == 1
    assert calls == ["custom", "completion", "waiting", "custom"]
