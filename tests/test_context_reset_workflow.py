import pytest

from evander.events.models import materialize_node_definition, WorkflowNodeEnvelope
from evander.gate_runtime import filter_when_matches
from evander.workflow.binding import resolve_workflow_bindings
from evander.workflow.context_reset import prepare_context_reset_pack
from evander.workflow.packs import compose_workflow_ir


def fixture_pack():
    nodes = [
        {
            "id": "inbound",
            "type": "gate:map",
            "config": {
                "template": {
                    "content": "message",
                    "cursor": "one",
                    "language": "English",
                }
            },
        },
        {
            "id": "effective",
            "type": "gate:map",
            "config": {
                "template": {
                    "selected_model": "fixture-model",
                    "has_additional_task": False,
                }
            },
        },
        {
            "id": "command",
            "type": "gate:switch",
            "config": {
                "cases": [
                    {
                        "port": "level",
                        "when": {"left": "${{inbound.content}}", "equals": "!LEVEL 1"},
                    }
                ],
                "default_port": "normal",
            },
        },
        {
            "id": "history",
            "type": "query:communication:messages",
            "config": {
                "communication_target_id": "assistant-chat",
                "limit": 8,
            },
        },
        {
            "id": "intent",
            "type": "query:classification:evaluate",
            "config": {
                "provider": "openai_decisions",
                "model": "gpt-6-luna",
                "state": {},
                "questions": {
                    "has_additional_task": {"type": "boolean", "instructions": "Does work remain?"},
                    "requested_level": {
                        "type": "choice",
                        "instructions": "Select the requested level.",
                        "criteria": {"1": "First model", "unchanged": "Keep model"},
                    },
                },
            },
        },
        {
            "id": "decode-task",
            "type": "gate:switch",
            "config": {
                "cases": [
                    {
                        "port": "only-controls",
                        "when": {
                            "not": {
                                "left": "${{intent.answers.has_additional_task.probability_true}}",
                                "greater_than": 0.3,
                            }
                        },
                    }
                ],
                "default_port": "task",
            },
        },
        {
            "id": "task-yes",
            "type": "gate:map",
            "config": {"template": {"has_additional_task": True}},
        },
        {
            "id": "task-no",
            "type": "gate:map",
            "config": {"template": {"has_additional_task": False}},
        },
        {"id": "intent-state", "type": "gate:merge", "config": {}},
        {
            "id": "select-level",
            "type": "gate:switch",
            "config": {
                "cases": [
                    {
                        "port": "switch",
                        "when": {
                            "left": "${{intent.answers.requested_level.choice}}",
                            "equals": "1",
                        },
                    }
                ],
                "default_port": "chat",
            },
        },
        {"id": "selected-level", "type": "gate:merge", "config": {}},
        {
            "id": "model",
            "type": "gate:map",
            "config": {
                "template": {
                    "model_id": "selected-model",
                    "has_additional_task": "${{selected-level.has_additional_task}}",
                }
            },
        },
        {"id": "model-command", "type": "gate:merge", "config": {}},
        {
            "id": "save-model",
            "type": "effect:session:update-metadata",
            "config": {
                "target": "assistant",
                "metadata": {"personal_assistant_model": "${{model-command.model_id}}"},
            },
        },
        {
            "id": "confirm-model",
            "type": "effect:communication:send-message",
            "config": {
                "communication_target_id": "assistant-chat",
                "message": "Model selected.",
            },
        },
        {"id": "model-ack", "type": "gate:merge", "config": {}},
        {
            "id": "followup",
            "type": "gate:switch",
            "config": {
                "cases": [
                    {
                        "port": "switch_only",
                        "when": {"left": "${{model-command.has_additional_task}}", "equals": False},
                    }
                ],
                "default_port": "with_task",
            },
        },
        {"id": "chat", "type": "gate:merge", "config": {}},
        {
            "id": "normal",
            "type": "gate:map",
            "config": {
                "template": {
                    "selected_model": "${{effective.selected_model}}",
                    "host_control_note": "",
                }
            },
        },
        {
            "id": "switched",
            "type": "gate:map",
            "config": {
                "template": {
                    "selected_model": "${{model-command.model_id}}",
                    "host_control_note": "Model saved.",
                }
            },
        },
        {"id": "context", "type": "gate:merge", "config": {}},
        {
            "id": "enqueue",
            "type": "effect:agent:enqueue-prompt",
            "config": {
                "session_id": "assistant",
                "dispatch_id": "message",
                "resume_mode": "same_context",
                "model": "${{context.selected_model}}",
                "prompt": "${{context.host_control_note}}\n${{inbound.content}}",
            },
        },
    ]
    for node in nodes:
        node["display_name"] = node["id"]
    routes = [
        ("inbound", "effective", None),
        ("effective", "command", None),
        ("command", "history", "normal"),
        ("command", "selected-level", "level"),
        ("history", "intent", None),
        ("intent", "decode-task", None),
        ("decode-task", "task-yes", "task"),
        ("decode-task", "task-no", "only-controls"),
        ("task-yes", "intent-state", None),
        ("task-no", "intent-state", None),
        ("intent", "chat", "error"),
        ("intent-state", "select-level", None),
        ("select-level", "selected-level", "switch"),
        ("select-level", "chat", "chat"),
        ("selected-level", "model", None),
        ("model", "model-command", None),
        ("model-command", "save-model", None),
        ("save-model", "confirm-model", None),
        ("confirm-model", "model-ack", None),
        ("confirm-model", "model-ack", "error"),
        ("model-ack", "followup", None),
        ("followup", "switched", "with_task"),
        ("switched", "context", None),
        ("chat", "normal", None),
        ("normal", "context", None),
        ("context", "enqueue", None),
    ]
    edges = [
        {"source_id": source, "target_id": target, **({"source_port": port} if port else {})}
        for source, target, port in routes
    ]
    return {
        "graphs": [{"id": "assistant-graph", "node_ids": [n["id"] for n in nodes]}],
        "ir": {"nodes": nodes, "edges": edges},
    }


def options():
    return dict(
        graph_id="assistant-graph",
        intent_query_id="intent",
        history_query_ids=["history"],
        command_switch_id="command",
        model_followup_gate_id="followup",
        request_context_id="context",
        enqueue_node_ids=["enqueue"],
        session_id_binding="assistant",
        current_model_binding="${{effective.selected_model}}",
        message_content_binding="${{inbound.content}}",
        request_id_binding="${{inbound.cursor}}",
        communication_target_id_binding="assistant-chat",
        preferred_language_binding="${{inbound.language}}",
        conversation_key_binding="teams:chat:one:agent_thread_id",
    )


def prepared():
    return prepare_context_reset_pack(fixture_pack(), **options())


def switch_port(pack, node_id, payload):
    config = next(n["config"] for n in pack["ir"]["nodes"] if n["id"] == node_id)
    names = {key: list(value) for key, value in payload.items()}
    for case in config["cases"]:
        predicate = resolve_workflow_bindings(case["when"], payload, names).resolved_config
        if filter_when_matches(predicate):
            return case["port"]
    return config["default_port"]


@pytest.mark.parametrize(
    "text", ["!RESET", " !reset ", "<p>!Reset&nbsp;</p>", "<p>&nbsp;!RESET</p>"]
)
def test_exact_reset_is_a_host_command(text):
    pack = prepared()
    assert switch_port(pack, "command", {"inbound": {"content": text}}) == "reset"
    assert (
        switch_port(
            pack,
            "conversation-reset-remaining-task",
            {"context": {"has_task_probability": 0}},
        )
        == "done"
    )
    assert not any(
        e["source_id"] == "conversation-reset-remaining-task" and e.get("source_port") == "done"
        for e in pack["ir"]["edges"]
    )


@pytest.mark.parametrize(
    "text",
    [
        '"!RESET"',
        "What does !RESET do?",
        "Do not !RESET",
        "Nollställ inte kontexten",
        "!RESET then summarize this",
        "Example: <code>!RESET</code>",
        "<code>!RESET</code>",
        "<pre>!RESET</pre>",
        "<blockquote><p>!RESET</p></blockquote>",
    ],
)
def test_only_a_whole_message_takes_the_unclassified_reset_shortcut(text):
    assert switch_port(prepared(), "command", {"inbound": {"content": text}}) == "normal"


@pytest.mark.parametrize(
    "probability,expected", [(0.69, "ordinary"), (0.7, "reset"), (0.99, "reset")]
)
def test_decisions_confidence_controls_mutation(probability, expected):
    payload = {"context": {"reset_value": True, "reset_probability": probability}}
    assert switch_port(prepared(), "conversation-reset-route", payload) == expected


@pytest.mark.parametrize(
    "language,expected", [("English", "en"), ("Swedish", "sv"), ("Svenska", "sv")]
)
def test_host_confirmation_language(language, expected):
    assert (
        switch_port(prepared(), "conversation-reset-language", {"inbound": {"language": language}})
        == expected
    )


def test_candidate_materializes_and_leaves_original_document_untouched():
    original = fixture_pack()
    candidate = prepare_context_reset_pack(original, **options())
    ir = compose_workflow_ir(project_dir=None, workflow_packs=[], inline_ir=candidate["ir"])
    for node in ir.effects + ir.queries + ir.gates:
        materialize_node_definition(WorkflowNodeEnvelope.model_validate(node))
    assert original == fixture_pack()
    assert (
        next(n for n in candidate["ir"]["nodes"] if n["id"] == "history")["config"][
            "context_session_id"
        ]
        == "assistant"
    )
    with pytest.raises(ValueError, match="already configured"):
        prepare_context_reset_pack(candidate, **options())


def test_projected_control_fields_have_no_conditional_or_missing_bindings():
    from evander.workflow.validation import validate_workflow_binding_diagnostics

    candidate = prepared()
    ir = compose_workflow_ir(project_dir=None, workflow_packs=[], inline_ir=candidate["ir"])
    config = {key: getattr(ir, key) for key in ("triggers", "gates", "effects", "queries", "edges")}
    assert validate_workflow_binding_diagnostics(config) == []


def test_exact_reset_projects_control_state_from_the_guaranteed_model_baseline():
    pack = prepared()
    nodes = {n["id"]: n for n in pack["ir"]["nodes"]}
    baseline = nodes["effective"]["config"]["template"]
    command = resolve_workflow_bindings(
        nodes["conversation-reset-command"]["config"]["template"],
        {"effective": baseline},
        {"effective": list(baseline)},
    ).resolved_config
    assert command["selected_model"] == "fixture-model"
    assert command["reset_value"] is True and command["reset_probability"] == 1
    assert command["has_task_probability"] == 0
    assert switch_port(pack, "conversation-reset-route", {"context": command}) == "reset"


def trace_control_actions(
    pack, *, content, answers=None, fail_reset=False, fail_confirmation=False, fail_binding=False
):
    """Replay the artifact's routes using the production binding/predicate engine."""
    nodes = {n["id"]: n for n in pack["ir"]["nodes"]}
    payloads = {"inbound": {"content": content, "cursor": "one", "language": "English"}}
    current, incoming = "effective", payloads["inbound"]
    actions, enqueued = [], []
    for _ in range(100):
        node = nodes[current]
        kind = node["type"]
        names = {key: list(value) for key, value in payloads.items()}
        port = None
        if kind == "gate:map":
            output = resolve_workflow_bindings(
                node["config"]["template"], payloads, names
            ).resolved_config
        elif kind == "gate:merge":
            output = dict(incoming)
        elif kind == "gate:switch":
            port = switch_port(pack, current, payloads)
            output = dict(incoming)
        elif kind == "query:communication:messages":
            output = {"messages": [], "success": True}
            if current == "conversation-reset-binding" and fail_binding:
                port = "error"
        elif kind == "query:classification:evaluate":
            assert answers is not None, "Exact host commands must bypass model evaluation"
            output = {"answers": answers, "success": True}
        elif kind == "effect:session:update-metadata":
            actions.append("model")
            output = {"success": True}
        elif kind == "effect:session:reset-context":
            actions.append("reset")
            output = {"success": not fail_reset, "pending": True, "generation": 1}
        elif kind == "effect:communication:send-message":
            action = (
                "model-confirm"
                if current == "confirm-model"
                else ("reset-failed" if "-failed-" in current else "reset-confirm")
            )
            actions.append(action)
            output = {"success": not fail_confirmation}
            if fail_confirmation:
                port = "error"
        elif kind == "effect:agent:enqueue-prompt":
            enqueued.append(
                resolve_workflow_bindings(node["config"], payloads, names).resolved_config
            )
            actions.append("enqueue")
            output = {"success": True}
        else:
            raise AssertionError(kind)
        payloads[current] = output
        edges = [
            e
            for e in pack["ir"]["edges"]
            if e["source_id"] == current and e.get("source_port") == port
        ]
        assert len(edges) <= 1, "The control route must not fan out a user's task"
        if not edges:
            return actions, enqueued
        incoming, current = output, edges[0]["target_id"]
    raise AssertionError("Control route did not terminate")


@pytest.mark.parametrize(
    "content,model,reset_probability,task_probability,expected",
    [
        ("!RESET", None, None, None, ["reset", "reset-confirm"]),
        ("Nollställ kontexten", "unchanged", 0.99, 0.01, ["reset", "reset-confirm"]),
        (
            "Reset your context, then summarize this",
            "unchanged",
            0.99,
            0.99,
            ["reset", "reset-confirm", "enqueue"],
        ),
        (
            "Switch to first and reset context",
            "1",
            0.99,
            0.01,
            ["model", "model-confirm", "reset", "reset-confirm"],
        ),
        (
            "Byt till första, nollställ kontexten och sammanfatta detta",
            "1",
            0.99,
            0.99,
            ["model", "model-confirm", "reset", "reset-confirm", "enqueue"],
        ),
        ("Switch to first", "1", 0.01, 0.01, ["model", "model-confirm"]),
        ("!LEVEL 1", None, None, None, ["model", "model-confirm"]),
        ("Explain the report", "unchanged", 0.01, 0.99, ["enqueue"]),
        ("Maybe reset context", "unchanged", 0.69, 0.01, ["enqueue"]),
        ("<code>!RESET</code>", "unchanged", 0.01, 0.99, ["enqueue"]),
    ],
)
def test_controls_run_in_order_and_accompanying_task_is_enqueued_once(
    content,
    model,
    reset_probability,
    task_probability,
    expected,
):
    answers = (
        None
        if model is None
        else {
            "requested_level": {"choice": model},
            "reset_context": {
                "value": reset_probability >= 0.5,
                "probability_true": reset_probability,
            },
            "has_additional_task": {
                "value": task_probability >= 0.5,
                "probability_true": task_probability,
            },
        }
    )
    actions, tasks = trace_control_actions(prepared(), content=content, answers=answers)
    assert actions == expected
    assert len(tasks) == expected.count("enqueue")
    if tasks:
        assert content in tasks[0]["prompt"]
        assert tasks[0]["model"] == ("selected-model" if model == "1" else "fixture-model")


@pytest.mark.parametrize(
    "failed,expected",
    [
        ("reset", ["reset", "reset-failed"]),
        ("confirmation", ["reset", "reset-confirm", "enqueue"]),
    ],
)
def test_reset_failure_blocks_task_but_confirmation_failure_retains_it(failed, expected):
    actions, tasks = trace_control_actions(
        prepared(),
        content="Reset context and explain this",
        answers={
            "requested_level": {"choice": "unchanged"},
            "reset_context": {"value": True, "probability_true": 0.99},
            "has_additional_task": {"value": True, "probability_true": 0.99},
        },
        fail_reset=failed == "reset",
        fail_confirmation=failed == "confirmation",
    )
    assert actions == expected
    assert len(tasks) == expected.count("enqueue")


@pytest.mark.parametrize("content", ["!RESET", "Reset context and explain this"])
def test_communication_binding_failure_prevents_reset_and_task(content):
    actions, tasks = trace_control_actions(
        prepared(),
        content=content,
        fail_binding=True,
        answers={
            "requested_level": {"choice": "unchanged"},
            "reset_context": {"value": True, "probability_true": 0.99},
            "has_additional_task": {"value": True, "probability_true": 0.99},
        },
    )
    assert actions == ["reset-failed"] and tasks == []
