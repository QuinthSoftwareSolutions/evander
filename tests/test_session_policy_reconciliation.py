import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from evander.admin.services.session_service import SessionAdminDependencies, SessionAdminService
from evander.config import GatewayConfig
from evander.directories import DirectoryService, DirectoryStore
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.session_reconcile_policy import SessionReconcilePolicyEffectProvider
from evander.events.models import WorkflowNodeEnvelope, materialize_node_definition
from evander.models import SessionManifest, ToolName, tool_name_value
from evander.policy import PolicyEngine
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.workflow.binding import resolve_workflow_bindings


@pytest.fixture
def setup(tmp_path):
    repository = JsonSessionManifestRepository(tmp_path / "sessions.json")
    session = SessionManifest(
        session_id="assistant-one", directory_path="root/team", model="model-one",
        expires_at=datetime.now(UTC) + timedelta(days=1),
        allowed_tools=["get_ado_backlog_item", "teams_post_status"],
        metadata={"agent_thread_id": "conversation-one", "http_targets": {"targets": {}},
                  "git_forwarding": {"enabled": True, "protected_refs": ["refs/heads/main"]}},
    )
    repository.upsert_session(session)
    other = session.model_copy(update={"session_id": "assistant-two"})
    repository.upsert_session(other)
    service = SessionAdminService(SessionAdminDependencies(
        runtime=SimpleNamespace(session_repository=repository),
        config=GatewayConfig(runtime_dir=tmp_path), directory_ownership=SimpleNamespace(),
    ))
    return service, repository, session, other


def test_reconciliation_preserves_identity_unmanaged_access_and_other_sessions(setup):
    service, repository, before, other = setup
    result = service.reconcile_session_policy("assistant-one", {
        "allowed_tools": ["get_ado_backlog_item", "teams_post_status", "create_ado_backlog_item"],
        "metadata": {"git_forwarding": {"enabled": False}},
    }, directory_path="root/team")
    after = repository.get_session("assistant-one")
    assert result["changed_fields"] == ["allowed_tools", "metadata"]
    assert {tool_name_value(t) for t in after.allowed_tools} == {
        "get_ado_backlog_item", "teams_post_status", "create_ado_backlog_item",
    }
    assert after.model_copy(update={"allowed_tools": before.allowed_tools,
                                    "metadata": before.metadata}) == before
    assert after.metadata["agent_thread_id"] == "conversation-one"
    assert after.metadata["git_forwarding"] == {
        "enabled": False, "protected_refs": ["refs/heads/main"],
    }
    assert repository.get_session("assistant-two") == other
    content = repository.path.read_bytes()
    modified = repository.path.stat().st_mtime_ns
    assert service.reconcile_session_policy("assistant-one", {
        "allowed_tools": ["get_ado_backlog_item", "teams_post_status", "create_ado_backlog_item"],
        "metadata": {"git_forwarding": {"enabled": False}},
    }, directory_path="root/team")["changed"] is False
    assert repository.path.read_bytes() == content
    assert repository.path.stat().st_mtime_ns == modified


@pytest.mark.parametrize("policy", [
    {"model": "different"}, {"allowed_tools": None}, {"allowed_tools": "create_ado_backlog_item"},
    {"metadata": []}, {"allowed_tools": [False]},
    {"allowed_tools": ["ToolName.TEAMS_POST_STATUS"]},
    {"approval_required_tools": "ToolName.TEAMS_POST_STATUS"},
])
def test_invalid_managed_policy_is_atomic(setup, policy):
    service, repository, _, _ = setup
    before = repository.path.read_bytes()
    with pytest.raises((ValueError, TypeError)):
        service.reconcile_session_policy("assistant-one", policy, directory_path="root/team")
    assert repository.path.read_bytes() == before


def test_tool_enum_inputs_are_saved_as_wire_names_and_authorize_after_reload(setup):
    service, repository, _, _ = setup
    service.reconcile_session_policy("assistant-one", {
        "allowed_tools": [ToolName.TEAMS_POST_STATUS, ToolName.CREATE_ADO_BACKLOG_ITEM],
        "approval_required_tools": [ToolName.TEAMS_POST_STATUS],
    }, directory_path="root/team")
    raw = json.loads(repository.path.read_text())["assistant-one"]
    assert raw["allowed_tools"] == ["teams_post_status", "create_ado_backlog_item"]
    assert raw["approval_required_tools"] == ["teams_post_status"]
    reloaded = JsonSessionManifestRepository(repository.path)
    policy = PolicyEngine(service._deps.config, reloaded, None)
    DirectoryService(DirectoryStore(service._deps.config.directories_store_path)).create(
        parent="root", name="team"
    )
    policy.require_tool("assistant-one", ToolName.TEAMS_POST_STATUS)
    policy.require_tool("assistant-one", ToolName.CREATE_ADO_BACKLOG_ITEM)
    assert policy.approval_required_for_tool(
        reloaded.get_session("assistant-one"), ToolName.TEAMS_POST_STATUS
    ) is True


def test_reconciliation_rejects_cross_directory_and_missing_sessions(setup):
    service, repository, _, _ = setup
    before = repository.path.read_bytes()
    with pytest.raises(ValueError, match="outside"):
        service.reconcile_session_policy("assistant-one", {"allowed_tools": []},
                                         directory_path="root/other")
    with pytest.raises(ValueError, match="Unknown session"):
        service.reconcile_session_policy("missing", {}, directory_path="root/team")
    assert repository.path.read_bytes() == before


def test_atomic_reconciliation_preserves_concurrent_conversation_updates(setup, monkeypatch):
    service, repository, _, _ = setup
    original = repository.mutate_session
    other_process = JsonSessionManifestRepository(repository.path)

    def concurrent_update(session_id, updater):
        current = other_process.get_session(session_id)
        other_process.upsert_session(current.model_copy(update={"metadata": {
            **current.metadata, "agent_thread_id": "conversation-two",
        }}))
        return original(session_id, updater)

    monkeypatch.setattr(repository, "mutate_session", concurrent_update)
    service.reconcile_session_policy("assistant-one", {"metadata": {"operator_label": "ready"}},
                                     directory_path="root/team")
    assert repository.get_session("assistant-one").metadata["agent_thread_id"] == "conversation-two"


def test_atomic_reconciliation_rejects_a_recreated_session(setup, monkeypatch):
    service, repository, _, _ = setup
    original = repository.mutate_session

    def recreate(session_id, updater):
        current = repository.get_session(session_id)
        repository.upsert_session(current.model_copy(update={
            "created_at": current.created_at + timedelta(seconds=1),
        }))
        return original(session_id, updater)

    monkeypatch.setattr(repository, "mutate_session", recreate)
    with pytest.raises(ValueError, match="replaced"):
        service.reconcile_session_policy("assistant-one", {"allowed_tools": []},
                                         directory_path="root/team")
    assert repository.get_session("assistant-one").allowed_tools


def test_reconciliation_retains_project_restrictions_and_approval_requirements(setup):
    service, repository, _, _ = setup
    service.reconcile_session_policy("assistant-one", {
        "allowed_tools": ["create_ado_backlog_item", "get_ado_backlog_item"],
        "approval_required_tools": ["create_ado_backlog_item"],
        "tool_argument_policies": {"create_ado_backlog_item": [{
            "id": "project-scope", "constraints": [{"id": "project", "path": "project",
            "operator": "allowlist", "values": ["Approved Project"]}],
        }]},
    }, directory_path="root/team")
    after = repository.get_session("assistant-one")
    assert [tool_name_value(t) for t in after.approval_required_tools] == ["create_ado_backlog_item"]
    constraint = after.tool_argument_policies["create_ado_backlog_item"][0].constraints[0]
    assert constraint.values == ["Approved Project"]
    service.reconcile_session_policy("assistant-one", {"allowed_tools": ["get_ado_backlog_item"]},
                                     directory_path="root/team")
    assert repository.get_session("assistant-one").approval_required_tools == []
    assert repository.get_session("assistant-one").tool_argument_policies


def test_real_binding_and_provider_apply_settings_before_the_next_turn(setup):
    service, repository, _, _ = setup
    definition = materialize_node_definition(WorkflowNodeEnvelope(
        id="reconcile", display_name="Reconcile", type="effect:session:reconcile-policy",
        config={"session_id": "${{created.session_id}}", "policy": "${{settings.policy}}"},
    ))
    resolved = resolve_workflow_bindings(
        definition.model_dump(),
        {"created": {"session_id": "assistant-one"},
         "settings": {"policy": {"allowed_tools": ["create_ado_backlog_item"]}}},
        {"created": {"session_id"}, "settings": {"policy"}},
    )
    provider = SessionReconcilePolicyEffectProvider()
    provider.initialize(definition, EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=repository.path.parent),
        session_policy_reconciler=service, metadata={"directory_path": "root/team"},
    ))
    assert provider.execute(resolved_config=resolved.resolved_config)["success"] is True
    assert [tool_name_value(t) for t in repository.get_session("assistant-one").allowed_tools] == [
        "create_ado_backlog_item",
    ]
    with pytest.raises(ValueError, match="outside workflow"):
        provider.execute(resolved_config={"session_id": "assistant-one", "policy": {},
                                          "directory_path": "root/other"})
