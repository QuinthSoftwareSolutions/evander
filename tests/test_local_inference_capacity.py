from datetime import UTC, datetime, timedelta
from pathlib import Path

from evander.local_inference_capacity import LocalInferenceCapacityStore


def test_local_inference_capacity_enforces_one_host_wide_limit(tmp_path: Path) -> None:
    store = LocalInferenceCapacityStore(tmp_path)

    first = store.claim(
        max_concurrency=1,
        owner_id="dispatch-1",
        model="qwen3.6:27b",
        session_id="session-1",
        dispatch_id="dispatch-1",
        execution_backend="libvirt_vm",
    )
    second = store.claim(
        max_concurrency=1,
        owner_id="dispatch-2",
        model="qwen3.6:27b",
        session_id="session-2",
        dispatch_id="dispatch-2",
        execution_backend="container_worker_vm",
    )

    assert first.admitted is True
    assert first.slot_id is not None
    assert second.admitted is False
    assert second.active_count == 1
    assert store.mark_running(first.slot_id) is True
    assert store.release(first.slot_id) is True
    assert store.claim(
        max_concurrency=1,
        owner_id="dispatch-2",
        model="qwen3.6:27b",
    ).admitted is True


def test_local_inference_capacity_is_unlimited_when_unconfigured(tmp_path: Path) -> None:
    admission = LocalInferenceCapacityStore(tmp_path).claim(
        max_concurrency=None,
        owner_id="dispatch-1",
        model="qwen3.6:27b",
    )

    assert admission.enabled is False
    assert admission.admitted is True
    assert admission.slot_id is None


def test_local_inference_capacity_reclaims_only_stale_launching_claims(
    tmp_path: Path,
) -> None:
    store = LocalInferenceCapacityStore(tmp_path)
    claimed_at = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    stale = store.claim(
        max_concurrency=1,
        owner_id="stale-launch",
        model="qwen3.6:27b",
        now=claimed_at,
    )
    assert stale.slot_id is not None

    replacement = store.claim(
        max_concurrency=1,
        owner_id="replacement",
        model="qwen3.6:27b",
        now=claimed_at + timedelta(minutes=6),
    )

    assert replacement.admitted is True
    assert replacement.slot_id != stale.slot_id


def test_local_inference_capacity_reconciles_only_authoritatively_terminal_runs(
    tmp_path: Path,
) -> None:
    store = LocalInferenceCapacityStore(tmp_path)
    terminal = store.claim(
        max_concurrency=3,
        owner_id="terminal",
        model="qwen3.6:27b",
        target_name="vm-terminal",
    )
    active = store.claim(
        max_concurrency=3,
        owner_id="active",
        model="qwen3.6:27b",
        target_name="vm-active",
    )
    indeterminate = store.claim(
        max_concurrency=3,
        owner_id="indeterminate",
        model="qwen3.6:27b",
        target_name="vm-indeterminate",
    )
    for admission in (terminal, active, indeterminate):
        assert store.mark_running(admission.slot_id) is True

    reconciled = store.reconcile_running(
        lambda lease: {
            "vm-terminal": False,
            "vm-active": True,
            "vm-indeterminate": None,
        }[str(lease.target_name)],
        minimum_age_seconds=0,
    )

    assert reconciled == 1
    remaining = store.read_all()
    assert terminal.slot_id not in remaining
    assert active.slot_id in remaining
    assert indeterminate.slot_id in remaining


def test_local_inference_capacity_keeps_running_lease_when_reconciliation_fails(
    tmp_path: Path,
) -> None:
    store = LocalInferenceCapacityStore(tmp_path)
    admission = store.claim(
        max_concurrency=1,
        owner_id="active",
        model="qwen3.6:27b",
    )
    assert store.mark_running(admission.slot_id) is True

    assert store.reconcile_running(
        lambda _lease: (_ for _ in ()).throw(RuntimeError("backend unavailable")),
        minimum_age_seconds=0,
    ) == 0
    assert admission.slot_id in store.read_all()


def test_local_inference_capacity_does_not_reconcile_fresh_running_lease(
    tmp_path: Path,
) -> None:
    store = LocalInferenceCapacityStore(tmp_path)
    now = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    admission = store.claim(
        max_concurrency=1,
        owner_id="fresh-run",
        model="qwen3.6:27b",
        now=now,
    )
    assert store.mark_running(admission.slot_id, now=now) is True

    assert store.reconcile_running(lambda _lease: False, now=now) == 0
    assert admission.slot_id in store.read_all()
