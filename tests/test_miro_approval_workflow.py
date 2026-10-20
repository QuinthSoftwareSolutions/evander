from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from evander.classification.models import ClassificationSettings
from evander.classification.service import ClassificationService
from evander.classification.switch import execute_switch, prepare_switch
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.state_update import StateUpdateEffectProvider
from evander.events.models import WorkflowNodeEnvelope
from evander.gate_runtime import filter_when_matches
from evander.workflow.binding import resolve_workflow_bindings
from evander_miro_addon.access_workflow import grant_access, inspect_access
from evander_miro_addon.approval_workflow import add_approval_fragment, approval_fragment
from evander_miro_addon.providers import register_providers
from evander_miro_addon.service import Access, MiroService
from test_miro_addon import run
from test_miro_addon import setup as setup
from test_miro_session_grants import inbox_message, peer


class RoutingProvider:
    supported_question_types = {"choice"}
    reports_option_probabilities = True

    def __init__(self, port, probability):
        self.port, self.probability = port, probability

    def evaluate(self, request, settings, api_key, record_attempt):
        choices = request.questions["route"].criteria
        return {
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": self.port,
                    "confidence": 1,
                    "probabilities": {
                        k: self.probability
                        if k == self.port
                        else (1 - self.probability) / (len(choices) - 1)
                        for k in choices
                    },
                }
            }
        }


def fragment():
    return approval_fragment(
        prefix="miro",
        session_id="peer",
        connection_id="architecture",
        message_cursor="${{source.cursor}}",
        approver_user_id="owner",
        message="${{source.content}}",
        language="sv",
        communication_target_id="personal",
        continue_node_id="continue",
    )


def walk(
    store,
    remote,
    cursor,
    *,
    port="other",
    probability=0.99,
    fail_delivery=False,
    conflict_pending=False,
):
    """Exercise real bindings, classification routing, grant and state providers.

    Only model choice and external message delivery are substituted.
    """
    from evander.messaging.teams_inbox_store import TeamsInboxStore

    f = fragment()
    nodes = {n["id"]: n for n in f["nodes"]}
    edges = f["edges"]
    message = TeamsInboxStore(store.runtime_dir / "teams-inbox-store.json").get_message(cursor)
    outputs = {"source": {"cursor": cursor, "content": message.content}}
    service = MiroService(
        store,
        Access("root/project", "workflow:approval", workflow=True, session_id="peer"),
        remote.client,
    )
    context = SimpleNamespace(
        metadata={"directory_path": "root/project", "graph_id": "personal", "node_id": "approve"}
    )
    state_context = EffectRuntimeContext(
        provider_config={"runtime_dir": str(store.runtime_dir)}, metadata=context.metadata
    )
    classifier = ClassificationService(
        providers={"openai_decisions": RoutingProvider(port, probability)},
        secret_resolver=lambda _: "test",
    )
    settings = ClassificationSettings(
        enabled=True, providers={"openai_decisions": {"enabled": True}}
    )
    current = f["entry_id"]
    previous = {}
    sent = []
    visited = []
    errors = []
    for _ in range(60):
        if current == "continue":
            return {"continued": previous, "sent": sent, "visited": visited, "errors": errors}
        node = nodes[current]
        visited.append(current)
        kind = node["type"]
        selected = None
        config = resolve_workflow_bindings(
            node["config"], outputs, {k: set(v) for k, v in outputs.items()}
        ).resolved_config
        try:
            if kind == "query:miro:board-access":
                result = inspect_access(context, service, config)
            elif kind == "effect:miro:grant-board-access":
                result = run(grant_access(context, service, config))
            elif kind == "gate:classifier:switch":
                decision = execute_switch(
                    classifier, settings, prepare_switch(classifier, settings, config)
                )
                selected = decision["selected_port"]
                result = previous
            elif kind == "gate:switch":
                selected = next(
                    (case["port"] for case in config["cases"] if filter_when_matches(case["when"])),
                    config["default_port"],
                )
                result = previous
            elif kind == "gate:map":
                result = config["template"]
            elif kind == "gate:merge":
                result = previous
            elif kind == "effect:state:update":
                provider = StateUpdateEffectProvider()
                provider.initialize(WorkflowNodeEnvelope.model_validate(node), state_context)
                if conflict_pending and config["operation"] == "set":
                    config["expected_version"] = 999
                result = provider.execute(resolved_config=config)
            elif kind == "effect:communication:send-message":
                if fail_delivery:
                    raise RuntimeError("Delivery failed")
                sent.append(config)
                result = {"success": True, "message_id": str(len(sent))}
            else:
                raise AssertionError(kind)
        except Exception as exc:
            errors.append((current, type(exc).__name__, str(exc)))
            selected = "error"
            result = {}
            if not any(
                e["source_id"] == current and e.get("source_port") == "error" for e in edges
            ):
                raise
        outputs[current] = result
        previous = result
        outgoing = [
            e for e in edges if e["source_id"] == current and e.get("source_port") == selected
        ]
        if not outgoing:
            return {"continued": None, "sent": sent, "visited": visited, "errors": errors}
        assert len(outgoing) == 1
        current = outgoing[0]["target_id"]
    raise AssertionError("Workflow did not terminate")


def test_request_question_confirmation_and_pending_task_continuation(setup):
    store, record, remote, _ = setup
    session = peer(store)
    first = inbox_message(
        store,
        "Please add the missing step to https://miro.com/app/board/team-board=/ "
        "using these requirements: <blockquote>Include the rollback step</blockquote>",
    )
    requested = walk(store, remote, first, port="request_edit")
    assert requested["errors"] == []
    assert requested["continued"] is None, requested
    assert (
        requested["sent"][0]["message"]
        == "Får jag läsa och redigera den här Miro-tavlan? https://miro.com/app/board/team-board=/"
    )
    assert store.session_grants(record, session) == []
    yes = inbox_message(store, "Yes, you may edit it", message_id="2")
    approved = walk(store, remote, yes, port="approve_edit")
    assert store.board_grant(record, session, "team-board=")["access"] == "edit"
    assert "missing step" in approved["continued"]["miro"]["request"]
    assert "Include the rollback step" in approved["continued"]["miro"]["request"]
    assert len(approved["sent"]) == 1 and "godkänt redigering" in approved["sent"][0]["message"]
    assert approved["sent"][0]["communication_target_id"] == "personal"
    assert remote.calls == []


def test_permission_only_finishes_and_combined_permission_continues_work(setup):
    store, record, remote, _ = setup
    session = peer(store)
    permission = inbox_message(store, "You may read https://miro.com/app/board/team-board=/")
    approved = walk(store, remote, permission, port="approve_read")
    assert approved["continued"] is None and len(approved["sent"]) == 1
    assert store.board_grant(record, session, "team-board=")["access"] == "read"
    combined = inbox_message(
        store,
        "You may edit https://miro.com/app/board/team-board=/ . Add the missing step.",
        message_id="2",
    )
    result = walk(store, remote, combined, port="approve_edit_task")
    assert "missing step" in result["continued"]["miro"]["request"]
    assert store.board_grant(record, session, "team-board=")["access"] == "edit"


@pytest.mark.parametrize(
    "port,text",
    [
        ("other", "I did not approve https://miro.com/app/board/team-board=/"),
        ("approve_edit", "You may edit https://miro.com/app/board/team-board=/"),
    ],
)
def test_default_and_low_confidence_routes_do_not_grant(setup, port, text):
    store, record, remote, _ = setup
    session = peer(store)
    cursor = inbox_message(store, text)
    result = walk(store, remote, cursor, port=port, probability=0.97)
    assert store.session_grants(record, session) == [] and result["sent"] == []


@pytest.mark.parametrize("failure", ["delivery", "state_conflict"])
def test_failed_prompt_does_not_activate_consent(setup, failure):
    store, record, remote, _ = setup
    session = peer(store)
    cursor = inbox_message(store, "Please edit https://miro.com/app/board/team-board=/")
    result = walk(
        store,
        remote,
        cursor,
        port="request_edit",
        fail_delivery=failure == "delivery",
        conflict_pending=failure == "state_conflict",
    )
    assert store.session_grants(record, session) == []
    assert not any(n.startswith("miro-activate") for n in result["visited"])
    if failure == "state_conflict":
        assert result["sent"] == []


def test_workflow_interfaces_validate_and_grant_is_never_an_mcp_tool():
    providers = register_providers()
    for node in fragment()["nodes"]:
        if ":miro:" in node["type"]:
            definition = providers.node_definition_resolvers[node["type"]](
                WorkflowNodeEnvelope.model_validate(node)
            )
            assert definition.session_id == "peer"
    from evander_miro_addon.mcp import MCP_TOOL_NAMES

    assert "miro_grant_board_access" not in MCP_TOOL_NAMES
    assert "miro_board_access" not in MCP_TOOL_NAMES


def test_operator_transform_preserves_other_configuration_and_rejects_duplicates():
    original_message = "${{source.content}}"
    pack = {
        "pack_id": "synthetic",
        "graphs": [
            {"id": "personal", "node_ids": ["source", "copy", "prepare", "enqueue", "pilot"]}
        ],
        "ir": {
            "version": "v1",
            "nodes": [
                {
                    "id": "source",
                    "type": "trigger:event",
                    "config": {"event_type": "sample.message"},
                },
                {
                    "id": "copy",
                    "type": "effect:agent:copy-files",
                    "config": {
                        "files": [
                            {
                                "destination_path": "/home/agent/AGENTS.md",
                                "content": "Keep original guidance.\n",
                            }
                        ]
                    },
                },
                {"id": "prepare", "type": "gate:map", "config": {"template": {"unchanged": True}}},
                {
                    "id": "enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "config": {
                        "prompt": "Original " + original_message,
                        "model": "model-a",
                        "fast_mode": False,
                    },
                },
                {
                    "id": "pilot",
                    "type": "effect:agent:enqueue-prompt",
                    "config": {
                        "prompt": "Pilot " + original_message,
                        "model": "model-b",
                        "reasoning_effort": "max",
                        "fast_mode": True,
                    },
                },
            ],
            "edges": [
                {"source_id": "source", "target_id": "copy"},
                {"source_id": "copy", "target_id": "prepare"},
                {"source_id": "prepare", "target_id": "enqueue"},
            ],
        },
    }
    baseline = deepcopy(pack)
    kwargs = {
        "graph_id": "personal",
        "source_node_id": "copy",
        "continue_node_id": "prepare",
        "enqueue_node_id": "enqueue",
        "additional_enqueue_node_ids": ["pilot"],
        "prefix": "miro",
        "session_id": "peer",
        "connection_id": "architecture",
        "message_cursor": "${{source.cursor}}",
        "approver_user_id": "owner",
        "message": original_message,
        "language": "en",
        "communication_target_id": "personal",
        "admin_url": "https://evander.example/addons/miro-addon/?directory_path=root/project",
    }
    candidate = add_approval_fragment(pack, **kwargs)
    assert pack == baseline
    before = {n["id"]: n for n in pack["ir"]["nodes"]}
    after = {n["id"]: n for n in candidate["ir"]["nodes"]}
    assert before["source"] == after["source"] and before["prepare"] == after["prepare"]
    assert after["copy"]["config"]["files"][0]["content"].startswith("Keep original guidance.")
    for node_id in ("enqueue", "pilot"):
        assert "miro-context.miro.request" in after[node_id]["config"]["prompt"]
        assert {k: v for k, v in after[node_id]["config"].items() if k != "prompt"} == {
            k: v for k, v in before[node_id]["config"].items() if k != "prompt"
        }
    assert candidate["ir"]["edges"][: len(pack["ir"]["edges"])] == [
        {"source_id": "source", "target_id": "copy"},
        {"source_id": "copy", "target_id": "miro-inspect"},
        {"source_id": "prepare", "target_id": "enqueue"},
    ]
    with pytest.raises(ValueError, match="duplicate"):
        add_approval_fragment(candidate, **kwargs)


def test_published_approval_example_compiles_with_valid_bindings(tmp_path):
    import json
    from pathlib import Path

    from evander.addons import AddonLoaderConfig
    from evander.config import TriggerOrchestratorWorkerGlobalConfig
    from evander.workflow.compiler import compile_trigger_orchestrator_config_with_diagnostics
    from evander.workflow.packs import WorkflowPack, compose_workflow_ir
    from evander.workflow.plugins import load_provider_plugins
    from evander.workflow.validation import validate_workflow_binding_diagnostics

    path = Path(__file__).resolve().parents[1] / "docs/examples/miro-session-approval.v1.json"
    pack = WorkflowPack.model_validate(json.loads(path.read_text()))
    catalog = load_provider_plugins(
        include_builtin=True,
        include_entry_points=False,
        addon_loader=AddonLoaderConfig(modules=["evander_miro_addon"], package_dirs=[]),
    )
    for item in pack.ir.nodes:
        resolver = catalog.node_definition_resolvers.get(item.node.type)
        if resolver:
            resolver(item.node)
    composed = compose_workflow_ir(
        project_dir=tmp_path, workflow_packs=[], inline_ir=pack.ir.model_dump(mode="json")
    )
    payload = composed.model_dump(exclude={"graphs"}) | {
        "runtime_dir": tmp_path,
        "workflow_graphs": pack.graphs,
        "addon_loader": {"modules": ["evander_miro_addon"], "package_dirs": []},
    }
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(payload)
    _, diagnostics = compile_trigger_orchestrator_config_with_diagnostics(config)
    assert diagnostics == []
    assert validate_workflow_binding_diagnostics(payload) == []
