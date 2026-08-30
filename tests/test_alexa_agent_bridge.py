from __future__ import annotations

import json
from pathlib import Path

from evander.config import OrchestrationCoreConfig
from evander.connectors.models import ConnectorDirectoryConfig
from evander.connectors.service import default_connector_registry
from evander.workflow.packs import compose_workflow_ir, load_workflow_pack
from evander.workflow.validation import validate_workflow_binding_diagnostics


PROJECT_DIR = Path(__file__).resolve().parents[1]


def test_alexa_agent_bridge_pack_is_valid_and_pins_the_agent_target() -> None:
    pack = load_workflow_pack(PROJECT_DIR, "alexa-agent-bridge.v1-example")
    composed = compose_workflow_ir(
        project_dir=PROJECT_DIR,
        workflow_packs=["alexa-agent-bridge.v1-example"],
    )
    effects = {effect["id"]: effect for effect in composed.effects}
    prompt_effect = effects["alexa-agent-prompt"]

    assert pack.pack_id == "alexa-agent-bridge"
    assert prompt_effect["type"] == "effect:agent:enqueue-prompt"
    assert prompt_effect["config"]["session_id"] == "REPLACE_WITH_FIXED_AGENT_SESSION_ID"
    assert "${{" not in prompt_effect["config"]["session_id"]
    assert prompt_effect["config"]["queue_key"] == (
        "alexa-agent:${{alexa-agent-dedupe-request.request_id}}"
    )
    assert prompt_effect["config"]["resume_mode"] == "new_context"
    assert "<untrusted_alexa_question>" in prompt_effect["config"]["prompt"]
    assert 'connector_id="alexa-reply"' in prompt_effect["config"]["prompt"]
    assert 'operation_name="submit"' in prompt_effect["config"]["prompt"]
    assert validate_workflow_binding_diagnostics(composed.model_dump()) == []

    OrchestrationCoreConfig.model_validate(
        {
            "runtime_dir": PROJECT_DIR / "runtime",
            "triggers": composed.triggers,
            "gates": composed.gates,
            "effects": composed.effects,
            "queries": composed.queries,
            "edges": composed.edges,
        }
    )


def test_alexa_reply_connector_is_one_exact_host_authenticated_write() -> None:
    path = PROJECT_DIR / "config" / "connectors" / "alexa-reply.json.example"
    config = ConnectorDirectoryConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
    connector = config.connectors["alexa-reply"]
    operation = connector.operations["submit"]

    assert config.auth_profiles["alexa-reply-callback"].type == "bearer_env"
    assert config.auth_profiles["alexa-reply-callback"].token_env == (
        "ALEXA_REPLY_CALLBACK_TOKEN"
    )
    assert connector.auth_profile == "alexa-reply-callback"
    assert set(connector.operations) == {"submit"}
    assert operation.access == "write"
    assert operation.requires_approval is False
    assert operation.config == {
        "method": "POST",
        "path": "/replies",
        "json_body_argument": "payload",
    }
    assert operation.input_schema["additionalProperties"] is False
    assert operation.input_schema["properties"]["payload"]["additionalProperties"] is False

    default_connector_registry().require("rest").validate_definition(connector)
