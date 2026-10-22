from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from evander.classification.models import ClassificationError, ClassificationSettings
from evander.classification.service import ClassificationService
from evander.classification.switch import ClassifierSwitchConfig, execute_switch, prepare_switch
from evander.events.models import GateClassifierSwitchDefinition


CONFIG = {
    "state": {"message": "Please use level 2"},
    "instructions": "Select the requested level, otherwise continue normal chat.",
    "cases": [{"port": "terra", "label": "Level 2", "description": "Requests level 2"}],
    "default_port": "standard",
}


@pytest.mark.parametrize(
    "patch",
    [
        {"cases": []},
        {"default_port": "terra"},
        {"default_port": "error"},
        {"default_port": "${{upstream.port}}"},
        {"instructions": " "},
        {"min_probability": True},
        {"cost_category": True},
        {"cost_category": 7},
        {"cost_category": []},
        {"min_probability": -0.1},
        {"min_probability": 1.1},
        {"cases": [{"port": "terra", "description": " "}]},
        {"cases": [{"port": "terra", "description": "A"}, {"port": "terra", "description": "B"}]},
    ],
)
def test_invalid_configuration(patch):
    with pytest.raises(ValidationError):
        ClassifierSwitchConfig.model_validate({**CONFIG, **patch})


def test_definition_accepts_bound_state_and_registers_schema():
    node = GateClassifierSwitchDefinition(
        id="smart",
        display_name="Smart switch",
        type="gate:classifier:switch",
        **{**CONFIG, "state": "${{incoming.message}}"},
    )
    assert node.state == "${{incoming.message}}"


class Provider:
    supported_question_types = {"choice"}

    def __init__(self, choice="terra", probability=0.8, missing=False):
        self.choice, self.probability, self.missing = choice, probability, missing

    def evaluate(self, request, settings, api_key, record_attempt):
        assert request.questions["route"].criteria == {
            "terra": "Requests level 2",
            "standard": "None of the described routes clearly applies.",
        }
        answer = {"type": "choice", "choice": self.choice, "confidence": 0.01}
        if not self.missing:
            answer["probabilities"] = {"terra": self.probability, "standard": 1 - self.probability}
        return {"answers": {"route": answer}}


@pytest.mark.parametrize(
    "choice,probability,minimum,expected,reason",
    [
        ("terra", 0.8, None, "terra", "choice"),
        ("standard", 0.2, None, "standard", "default"),
        ("terra", 0.8, 0.8, "terra", "choice"),
        ("terra", 0.8, 0.81, "standard", "below_probability"),
        ("terra", 1, 1, "terra", "choice"),
        ("terra", 0, 0, "terra", "choice"),
    ],
)
def test_routing_uses_option_probability(choice, probability, minimum, expected, reason):
    settings = ClassificationSettings(enabled=True)
    service = ClassificationService(
        providers={"jev": Provider(choice, probability)}, secret_resolver=lambda _: "test"
    )
    payload = copy.deepcopy({**CONFIG, "min_probability": minimum})
    prepared = prepare_switch(service, settings, payload)
    payload["cases"][0]["description"] = "Changed later"
    result = execute_switch(service, settings, prepared)
    assert result["selected_choice"] == choice
    assert result["selected_port"] == expected
    assert result["routing_reason"] == reason


@pytest.mark.parametrize("minimum", [None, 0.8])
def test_provider_without_probabilities(minimum):
    settings = ClassificationSettings(enabled=True)
    service = ClassificationService(
        providers={"jev": Provider(missing=True)}, secret_resolver=lambda _: "test"
    )
    prepared = prepare_switch(service, settings, {**CONFIG, "min_probability": minimum})
    if minimum is None:
        assert execute_switch(service, settings, prepared)["selected_port"] == "terra"
    else:
        with pytest.raises(ClassificationError, match="requires option probabilities"):
            execute_switch(service, settings, prepared)


def test_unknown_choice_is_rejected():
    settings = ClassificationSettings(enabled=True)
    service = ClassificationService(
        providers={"jev": Provider(choice="unknown")}, secret_resolver=lambda _: "test"
    )
    with pytest.raises(ClassificationError, match="outside the supplied"):
        execute_switch(service, settings, prepare_switch(service, settings, CONFIG))


def test_example_compiles_with_known_ports_and_passthrough_bindings(tmp_path):
    import json
    from pathlib import Path

    from config_helpers import canonical_worker_config
    from evander.workflow.compiler import compile_trigger_orchestrator_config_with_diagnostics
    from evander.workflow.packs import load_workflow_pack
    from evander.workflow.validation import (
        derive_workflow_output_schemas,
        validate_workflow_binding_diagnostics,
    )

    path = Path(__file__).resolve().parents[1] / "docs/examples/classifier-level-switch.json"
    pack = load_workflow_pack(path.parents[2], str(path))
    payload = {
        "runtime_dir": tmp_path,
        "triggers": [],
        "gates": [],
        "effects": [],
        "queries": [],
        "edges": [edge.model_dump() for edge in pack.ir.edges],
        "workflow_graphs": [graph.model_dump() for graph in pack.graphs],
    }
    for ir_node in pack.ir.nodes:
        node = ir_node.node
        key = "triggers" if node.type.startswith("trigger:") else "gates"
        payload[key].append(node.model_dump())
    worker_config = canonical_worker_config(payload)
    _, diagnostics = compile_trigger_orchestrator_config_with_diagnostics(worker_config)
    assert diagnostics == []
    assert worker_config.triggers[0].config["enabled"] is False
    assert validate_workflow_binding_diagnostics(payload) == []
    assert derive_workflow_output_schemas(payload)["choose-level"]["message"] == {"type": "string"}
    samples = json.loads(path.with_name("classifier-level-samples.json").read_text())
    assert {s["expected_port"] for s in samples} == {
        "luna",
        "terra",
        "sol",
        "astra",
        "standard",
        "invalid",
    }
    bad = copy.deepcopy(payload)
    next(edge for edge in bad["edges"] if edge["source_id"] == "choose-level")["source_port"] = (
        "unknown"
    )
    _, diagnostics = compile_trigger_orchestrator_config_with_diagnostics(
        canonical_worker_config(bad)
    )
    assert any(
        d.node_type == "gate:classifier:switch" and "unknown" in d.message for d in diagnostics
    )
