"""Saved execution preferences must be inherited by every later dispatch channel."""

from datetime import timedelta
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evander.admin.composition import build_admin_runtime
from evander.directories import DirectoryService
from evander.effects.registry import EffectProviderFactory
from evander.events.models import WorkflowNodeEnvelope
from evander.events.provider_models import SessionSetModelSettingsEffectDefinition
from evander.fast_mode import resolve_fast_mode
from evander.models import SessionManifest
from evander.providers.builtin import register_providers
from evander.queries.registry import QueryProviderFactory
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.schema_bootstrap import _QUERY_OUTPUT_SCHEMAS
from evander.workflow.binding import resolve_workflow_bindings
from jsonschema import validate


def runtime(tmp_path: Path):
    admin = build_admin_runtime(tmp_path / "runtime")
    directories = DirectoryService(admin.directory_store)
    directories.create(parent="root", name="team-a")
    directories.create(parent="root", name="team-b")
    directories.create(parent="root/team-a", name="child")
    admin.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="assistant-a",
            vm_id="vm-a",
            directory_path="root/team-a",
            model="gpt-6-luna",
            reasoning_effort="low",
            fast_mode=True,
            allowed_tools=[],
            expires_at="2099-01-01T00:00:00Z",
            metadata={"keep": "unchanged"},
        )
    )
    return admin, EffectProviderFactory(admin.runtime.provider_config, admin_service=admin)


def definition(**fields):
    return SessionSetModelSettingsEffectDefinition.model_validate(
        {
            "id": "save-preferences",
            "display_name": "Save preferences",
            "type": "effect:session:set-model-settings",
            **fields,
        }
    )


def test_plugin_registration_exposes_the_model_settings_contract():
    resolver = register_providers()["node_definition_resolvers"][
        "effect:session:set-model-settings"
    ]
    with pytest.raises(ValidationError, match="settings"):
        resolver(
            WorkflowNodeEnvelope(
                id="save",
                display_name="Save model",
                type="effect:session:set-model-settings",
                config={"target": "assistant-a", "settings": {"allowed_tools": []}},
            )
        )


def test_saved_model_and_speed_are_inherited_without_prompt_overrides(tmp_path):
    admin, factory = runtime(tmp_path)
    effect = factory.build(
        WorkflowNodeEnvelope(
            id="save-preferences",
            display_name="Save preferences",
            type="effect:session:set-model-settings",
            config={"session_id": "assistant-a", "settings": "${{input.settings}}"},
        ),
        directory_path="root/team-a",
    )
    result = effect.execute(
        resolved_config={"settings": {"model": "gpt-6.1-terra", "fast_mode": False}}
    )
    assert result["success"] is True and result["session_id"] == "assistant-a"
    reopened = build_admin_runtime(tmp_path / "runtime")
    session = reopened.runtime.session_repository.get_session("assistant-a")
    assert reopened.vm_agent_admin.resolve_agent_model_for_session("assistant-a") == "gpt-6.1-terra"
    assert resolve_fast_mode({}, session=session, harness="codex").value is False
    assert session.reasoning_effort == "low"
    assert session.metadata == {"keep": "unchanged"} and session.allowed_tools == []


def test_setting_only_model_preserves_speed_and_can_clear_the_override(tmp_path):
    admin, factory = runtime(tmp_path)
    admin.operations_admin.write_directory_domain_config(
        "root", "issue_setup", {"default_model": "gpt-6.1-sol"}
    )
    effect = factory.build(
        definition(target="assistant-a", settings={"model": "gpt-6.1-terra"}), directory_path="root"
    )
    assert effect.execute()["success"]
    session = admin.runtime.session_repository.get_session("assistant-a")
    assert session.model == "gpt-6.1-terra" and session.fast_mode is True
    assert effect.execute(resolved_config={"settings": {"model": None}})["success"]
    assert admin.runtime.session_repository.get_session("assistant-a").model is None
    assert admin.vm_agent_admin.resolve_agent_model_for_session("assistant-a") == "gpt-6.1-sol"


@pytest.mark.parametrize("model", [None, "gpt-6.1-terra", "custom-model-alias"])
def test_speed_only_update_preserves_explicit_and_inherited_models(tmp_path, model):
    admin, factory = runtime(tmp_path)
    admin.operations_admin.write_directory_domain_config(
        "root", "issue_setup", {"default_model": "gpt-6-astra"}
    )
    admin.session_admin.update_session_model_settings("assistant-a", {"model": model})
    effect = factory.build(
        definition(target="assistant-a", settings={"fast_mode": False}),
        directory_path="root/team-a",
    )
    assert effect.execute()["success"]
    context = admin.issue_git_admin.session_context_query(target="assistant-a")
    assert context["model"] == model
    validate(context["model"], _QUERY_OUTPUT_SCHEMAS["query:session-context"]["model"])
    assert admin.runtime.session_repository.get_session("assistant-a").fast_mode is False
    assert admin.vm_agent_admin.resolve_agent_model_for_session("assistant-a") == (
        model if model is not None else "gpt-6-astra"
    )


def test_workflow_reads_and_dispatches_the_canonical_model_after_retiring_metadata(tmp_path):
    admin, effects = runtime(tmp_path)
    admin.issue_git_admin.session_update_metadata(
        target="assistant-a", metadata={"old_model_preference": "gpt-6-luna"}
    )
    effects.build(
        definition(target="assistant-a", settings={"model": "gpt-6.1-terra"}),
        directory_path="root/team-a",
    ).execute()
    queries = QueryProviderFactory(admin.runtime.provider_config, admin_service=admin)
    query = queries.build(
        WorkflowNodeEnvelope(
            id="session",
            display_name="Read session",
            type="query:session-context",
            config={"target": "assistant-a"},
        ),
        directory_path="root/team-a",
    )
    context = query.execute()
    assert context["model"] == "gpt-6.1-terra"
    assert context["metadata"]["old_model_preference"] == "gpt-6-luna"
    effects.build(
        WorkflowNodeEnvelope(
            id="retire-preference",
            display_name="Retire old preference",
            type="effect:session:reconcile-policy",
            config={
                "session_id": "assistant-a",
                "policy": {"remove_metadata_keys": ["old_model_preference"]},
            },
        ),
        directory_path="root/team-a",
    ).execute()
    # A subsequent operator model change is immediately visible to the workflow
    # and ordinary dispatch, without maintaining a second preference field.
    admin.session_admin.set_session_model("assistant-a", "gpt-6.1-sol")
    context = query.execute()
    resolved = resolve_workflow_bindings(
        {"current_model": "${{session.model}}"},
        {"session": context},
        {"session": {"model"}},
    ).resolved_config
    assert resolved["current_model"] == "gpt-6.1-sol"
    assert admin.vm_agent_admin.resolve_agent_model_for_session("assistant-a") == "gpt-6.1-sol"
    assert context["metadata"] == {"keep": "unchanged"}


@pytest.mark.parametrize("delete_session", [False, True])
def test_preferences_preserve_concurrent_updates_and_do_not_resurrect_sessions(
    tmp_path, monkeypatch, delete_session
):
    admin, factory = runtime(tmp_path)
    other_writer = build_admin_runtime(tmp_path / "runtime")
    original_lookup = admin.session_admin._sessions_by_id

    def stale_lookup():
        snapshot = original_lookup()
        if delete_session:
            other_writer.runtime.session_repository.delete_session("assistant-a")
        else:
            other_writer.session_admin.update_session_model_settings(
                "assistant-a", {"fast_mode": False}
            )
            other_writer.session_admin.reconcile_session_policy(
                "assistant-a",
                {"metadata": {"concurrent": "preserved"}},
                directory_path="root/team-a",
            )
        return snapshot

    monkeypatch.setattr(admin.session_admin, "_sessions_by_id", stale_lookup)
    effect = factory.build(
        definition(target="assistant-a", settings={"model": "gpt-6.1-terra"}),
        directory_path="root/team-a",
    )
    if delete_session:
        with pytest.raises(ValueError, match="Unknown session"):
            effect.execute()
        assert other_writer.runtime.session_repository.get_session("assistant-a") is None
    else:
        assert effect.execute()["success"]
        saved = other_writer.runtime.session_repository.get_session("assistant-a")
        assert saved.model == "gpt-6.1-terra"
        assert saved.fast_mode is False
        assert saved.metadata == {"keep": "unchanged", "concurrent": "preserved"}


@pytest.mark.parametrize(
    "settings",
    [
        {"model": ""},
        {"model": 12},
        {"model": "gpt-6.1-terra", "fast_mode": "fast"},
        {"model": "gpt-6.1-terra", "reasoning_effort": "automatic"},
        {},
        {"allowed_tools": []},
    ],
)
def test_invalid_settings_leave_all_persisted_defaults_unchanged(tmp_path, settings):
    admin, factory = runtime(tmp_path)
    effect = factory.build(
        definition(target="assistant-a", settings={"model": "gpt-6.1-terra"}), directory_path="root"
    )
    before = admin.runtime.session_repository.get_session("assistant-a").model_dump()
    with pytest.raises(ValueError):
        effect.execute(resolved_config={"settings": settings})
    assert admin.runtime.session_repository.get_session("assistant-a").model_dump() == before


@pytest.mark.parametrize("scope", ["root/team-b", "root/team-a/child"])
def test_workflow_cannot_change_another_directorys_session(tmp_path, scope):
    admin, factory = runtime(tmp_path)
    effect = factory.build(
        definition(vm_name="vm-a", settings={"model": "gpt-6.1-terra"}), directory_path=scope
    )
    with pytest.raises(ValueError, match="outside workflow directory scope"):
        effect.execute()
    assert admin.runtime.session_repository.get_session("assistant-a").model == "gpt-6-luna"


@pytest.mark.parametrize("mutation", ["move", "replace"])
@pytest.mark.parametrize("timing", ["before_service_lookup", "before_locked_update"])
@pytest.mark.parametrize("kind", ["model-settings", "reasoning-effort"])
@pytest.mark.parametrize("legacy", [False, True])
def test_settings_update_rejects_stale_session_identity_and_directory(
    tmp_path, monkeypatch, mutation, timing, kind, legacy
):
    admin, factory = runtime(tmp_path)
    repository = admin.runtime.session_repository
    if legacy:
        payload = json.loads(repository.path.read_text())
        del payload["assistant-a"]["created_at"]
        repository.path.write_text(json.dumps(payload))
    other = JsonSessionManifestRepository(repository.path)
    config = {"target": "assistant-a"}
    if kind == "model-settings":
        config["settings"] = {"model": "gpt-6.1-terra"}
    else:
        config["reasoning_effort"] = "high"
    effect = factory.build(
        WorkflowNodeEnvelope(
            id="save",
            display_name="Save settings",
            type=f"effect:session:set-{kind}",
            config=config,
        ),
        directory_path="root/team-a",
    )

    def change_target():
        current = other.get_session("assistant-a")
        changes = (
            {"directory_path": "root/team-b"}
            if mutation == "move"
            else {"created_at": current.created_at + timedelta(seconds=1)}
        )
        other.upsert_session(current.model_copy(update=changes))

    if timing == "before_service_lookup":
        lookup = effect.runtime_context.session_lookup
        read = lookup.read_all_sessions

        def read_stale_snapshot():
            snapshot = read()
            change_target()
            return snapshot

        monkeypatch.setattr(lookup, "read_all_sessions", read_stale_snapshot)
    else:
        mutate = repository.mutate_session

        def change_before_lock(session_id, updater):
            change_target()
            return mutate(session_id, updater)

        monkeypatch.setattr(repository, "mutate_session", change_before_lock)
    with pytest.raises(ValueError, match="replaced|validated directory"):
        effect.execute()
    saved = other.get_session("assistant-a")
    assert saved.model == "gpt-6-luna"
    assert saved.reasoning_effort == "low"


def test_model_settings_can_upgrade_an_unchanged_legacy_manifest(tmp_path):
    admin, factory = runtime(tmp_path)
    repository = admin.runtime.session_repository
    payload = json.loads(repository.path.read_text())
    del payload["assistant-a"]["created_at"]
    repository.path.write_text(json.dumps(payload))
    effect = factory.build(
        definition(target="assistant-a", settings={"model": "gpt-6.1-terra"}),
        directory_path="root/team-a",
    )
    assert effect.execute()["success"]
    updated = repository.get_session("assistant-a")
    assert updated.model == "gpt-6.1-terra"
    assert "created_at" in updated.model_fields_set


@pytest.mark.parametrize(
    "fields",
    [
        {"settings": {"model": "gpt-6.1-terra"}},
        {"target": " "},
        {"target": "assistant-a"},
        {"target": "assistant-a", "settings": {}},
        {"target": "assistant-a", "settings": {"unknown": True}},
        {"target": "assistant-a", "session_id": "assistant-b", "settings": {"fast_mode": True}},
    ],
)
def test_model_settings_contract_requires_one_target_and_supported_settings(fields):
    with pytest.raises(ValidationError):
        definition(**fields)
