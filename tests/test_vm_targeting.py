from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.effects.providers.agent_session import resolve_vm_target as legacy_resolve_vm_target
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.vm_targeting import (
    ResolvedVmTarget,
    read_all_sessions,
    resolve_session_id_from_vm_name,
    resolve_vm_name_from_session,
    resolve_vm_target,
)


_MISSING = object()


@dataclass
class _SessionLookupStub:
    sessions: dict[str, object]

    def read_all_sessions(self) -> dict[str, object]:
        return dict(self.sessions)


def _build_context(
    tmp_path: Path,
    *,
    sessions: dict[str, object] | None = None,
    session_lookup: object = _MISSING,
) -> EffectRuntimeContext:
    lookup = _SessionLookupStub(sessions or {}) if session_lookup is _MISSING else session_lookup
    return EffectRuntimeContext(
        provider_config={"runtime_dir": tmp_path},
        session_lookup=lookup,
        admin_service=SimpleNamespace(),
    )


def test_resolve_vm_target_from_session_id_returns_canonical_resolution(tmp_path: Path) -> None:
    context = _build_context(
        tmp_path,
        sessions={"session-1": SimpleNamespace(vm_id="lab-vm-1")},
    )

    resolved = resolve_vm_target(
        context,
        {"session_id": "session-1"},
        effect_type="effect:vm:exec",
    )

    assert resolved == ResolvedVmTarget(
        vm_name="lab-vm-1",
        session_id="session-1",
        operation_target="session-1",
        source="session_id",
    )


@pytest.mark.parametrize(
    ("sessions", "payload", "expected"),
    [
        (
            {"session-2": SimpleNamespace(vm_id="lab-vm-2")},
            {"vm_name": "lab-vm-2"},
            ResolvedVmTarget(
                vm_name="lab-vm-2",
                session_id="session-2",
                operation_target="lab-vm-2",
                source="vm_name",
            ),
        ),
        (
            {},
            {"vm_name": "standalone-vm"},
            ResolvedVmTarget(
                vm_name="standalone-vm",
                session_id="session-standalone-vm",
                operation_target="standalone-vm",
                source="vm_name",
            ),
        ),
    ],
)
def test_resolve_vm_target_from_vm_name_uses_vm_name_as_operation_target(
    tmp_path: Path,
    sessions: dict[str, object],
    payload: dict[str, object],
    expected: ResolvedVmTarget,
) -> None:
    context = _build_context(tmp_path, sessions=sessions)

    resolved = resolve_vm_target(context, payload, effect_type="effect:vm:exec")

    assert resolved == expected


@pytest.mark.parametrize(
    ("sessions", "payload", "expected"),
    [
        (
            {"session-3": SimpleNamespace(vm_id="lab-vm-3")},
            {"target": "session-3"},
            ResolvedVmTarget(
                vm_name="lab-vm-3",
                session_id="session-3",
                operation_target="session-3",
                source="target_session_id",
            ),
        ),
        (
            {"session-4": SimpleNamespace(vm_id="other-vm")},
            {"target": "lab-vm-4"},
            ResolvedVmTarget(
                vm_name="lab-vm-4",
                session_id="session-lab-vm-4",
                operation_target="lab-vm-4",
                source="target_vm_name",
            ),
        ),
    ],
)
def test_resolve_vm_target_from_target_handles_session_and_vm_inputs(
    tmp_path: Path,
    sessions: dict[str, object],
    payload: dict[str, object],
    expected: ResolvedVmTarget,
) -> None:
    context = _build_context(tmp_path, sessions=sessions)

    resolved = resolve_vm_target(context, payload, effect_type="effect:vm:delete-files")

    assert resolved == expected


def test_resolve_vm_name_from_session_errors_for_unknown_session(tmp_path: Path) -> None:
    context = _build_context(tmp_path, sessions={"session-5": SimpleNamespace(vm_id="lab-vm-5")})

    with pytest.raises(RuntimeError, match="Unknown session: missing-session"):
        resolve_vm_name_from_session(context, "missing-session")


def test_resolve_session_id_from_vm_name_rejects_duplicates(tmp_path: Path) -> None:
    context = _build_context(
        tmp_path,
        sessions={
            "session-a": SimpleNamespace(vm_id="shared-vm"),
            "session-b": SimpleNamespace(vm_id="shared-vm"),
        },
    )

    with pytest.raises(
        RuntimeError,
        match=r"VM 'shared-vm' is associated with multiple sessions: session-a, session-b",
    ):
        resolve_session_id_from_vm_name(context, "shared-vm")


def test_read_all_sessions_requires_session_lookup(tmp_path: Path) -> None:
    context = _build_context(tmp_path, session_lookup=None)

    with pytest.raises(RuntimeError, match="effect:vm:exec requires session_lookup"):
        read_all_sessions(context, effect_type="effect:vm:exec")


def test_legacy_resolve_vm_target_wrapper_returns_tuple(tmp_path: Path) -> None:
    context = _build_context(
        tmp_path,
        sessions={"session-6": SimpleNamespace(vm_id="lab-vm-6")},
    )

    vm_name, session_id = legacy_resolve_vm_target(
        context,
        {"target": "session-6"},
        effect_type="effect:agent:copy-files",
    )

    assert (vm_name, session_id) == ("lab-vm-6", "session-6")
