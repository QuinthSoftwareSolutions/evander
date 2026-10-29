from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from evander.admin.composition import build_admin_runtime
from evander.directories import DirectoryService
from evander.effects.registry import EffectProviderFactory
from evander.events.models import WorkflowNodeEnvelope
from evander.events.provider_models import SessionSetReasoningEffortEffectDefinition
from evander.models import SessionManifest
from evander.reasoning_effort import resolve_reasoning_effort


def _definition(**fields):
    return SessionSetReasoningEffortEffectDefinition.model_validate(
        {
            "id": "save-effort",
            "display_name": "Save effort",
            "type": "effect:session:set-reasoning-effort",
            **fields,
        }
    )


def _runtime(tmp_path: Path):
    runtime = build_admin_runtime(tmp_path / "runtime")
    directories = DirectoryService(runtime.directory_store)
    directories.create(parent="root", name="team-a")
    directories.create(parent="root", name="team-b")
    directories.create(parent="root/team-a", name="child")
    runtime.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="assistant-a",
            vm_id="vm-a",
            directory_path="root/team-a",
            model="gpt-5.4",
            allowed_tools=[],
            expires_at="2099-01-01T00:00:00Z",
            metadata={"keep": "unchanged"},
        )
    )
    return runtime, EffectProviderFactory(
        runtime.runtime.provider_config,
        admin_service=runtime,
    )


def test_workflow_sets_and_clears_persistent_native_default(tmp_path: Path) -> None:
    runtime, factory = _runtime(tmp_path)
    # Exercise envelope materialization and the composed service/port wiring too.
    provider = factory.build(
        WorkflowNodeEnvelope(
            id="save-effort",
            display_name="Save effort",
            type="effect:session:set-reasoning-effort",
            config={"session_id": "assistant-a", "reasoning_effort": "${{input.effort}}"},
        ),
        directory_path="root/team-a",
    )
    for effort, effective, source in [("low", "low", "session"), (None, "high", "default")]:
        result = provider.execute(resolved_config={"reasoning_effort": effort})
        assert result["success"] is True
        assert result["reasoning_effort"] == effort
        # Reopen through the supported composition boundary to prove persistence.
        reopened = build_admin_runtime(tmp_path / "runtime")
        stored = reopened.runtime.session_repository.get_session("assistant-a")
        assert stored is not None
        assert stored.reasoning_effort == effort
        assert stored.model == "gpt-5.4"
        assert stored.metadata == {"keep": "unchanged"}
        assert stored.allowed_tools == []
        context = reopened.issue_git_admin.session_context_query(target="assistant-a")
        assert context["reasoning_effort"] == effort
        resolved = resolve_reasoning_effort({}, session=stored, harness="codex", model=stored.model)
        assert (resolved.value, resolved.source) == (effective, source)


@pytest.mark.parametrize("scope", ["root/team-b", "root/team-a/child"])
def test_workflow_cannot_set_effort_outside_its_directory(tmp_path: Path, scope: str) -> None:
    runtime, factory = _runtime(tmp_path)
    provider = factory.build(
        _definition(vm_name="vm-a", reasoning_effort="low"), directory_path=scope
    )
    with pytest.raises(ValueError, match="outside workflow directory scope"):
        provider.execute()
    assert runtime.runtime.session_repository.get_session("assistant-a").reasoning_effort is None


def test_unsupported_effort_leaves_stored_default_unchanged(tmp_path: Path) -> None:
    runtime, factory = _runtime(tmp_path)
    provider = factory.build(
        _definition(target="assistant-a", reasoning_effort="low"), directory_path="root"
    )
    provider.execute()
    with pytest.raises(ValueError, match="support"):
        provider.execute(resolved_config={"reasoning_effort": "max"})
    assert runtime.runtime.session_repository.get_session("assistant-a").reasoning_effort == "low"
    with pytest.raises(ValueError, match="Unknown session target"):
        provider.execute(resolved_config={"target": "missing"})


@pytest.mark.parametrize(
    "fields",
    [
        {"session_id": "assistant-a"},
        {"session_id": "assistant-a", "reasoning_effort": "automatic"},
        {"session_id": "assistant-a", "reasoning_effort": False},
        {"session_id": "assistant-a", "reasoning_effort": {}},
        {"reasoning_effort": "low"},
        {"session_id": " ", "reasoning_effort": "low"},
        {"session_id": "assistant-a", "target": "assistant-b", "reasoning_effort": "low"},
    ],
)
def test_effect_definition_rejects_missing_effort_or_ambiguous_target(fields) -> None:
    with pytest.raises(ValidationError):
        _definition(**fields)


def test_effect_definition_accepts_explicit_clear_and_bindings() -> None:
    assert _definition(session_id="assistant-a", reasoning_effort=None).reasoning_effort is None
    assert (
        _definition(
            session_id="${{query.session_id}}", reasoning_effort="${{input.effort}}"
        ).reasoning_effort
        == "${{input.effort}}"
    )
