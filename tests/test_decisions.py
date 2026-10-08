from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
import json

import httpx

import pytest

from config_helpers import canonical_worker_config, effect_execution_payload, gate_execution_payload
from test_classification_integration import call_tool, environment as environment, worker_for
from evander.classification.decisions import DecisionsProvider
from evander.classification.models import (
    DECISIONS_PROVIDER_ID,
    ClassificationError,
    ClassificationSettings,
    PreparedEvaluation,
    Question,
    answer_schema,
    validate_answers,
)
from evander.classification.service import ClassificationService
from evander.classification.switch import execute_switch, prepare_switch
from evander.events.models import TriggerEvent
from evander.gateway import create_server
from evander.gates.registry import GateProviderFactory
from evander.model_usage.runtime import model_usage_store
from evander.models import SessionManifest, ToolName


QUESTIONS = {
    "route": {
        "type": "choice",
        "instructions": {"task": "Choose a team"},
        "criteria": {"support": "Product issues", "sales": "Purchases"},
    },
    "severity": {"type": "score", "instructions": "Impact?", "criteria": ["Low", "High"]},
    "urgent": {
        "type": "boolean",
        "instructions": "Urgent?",
        "criteria": {"false": "Can wait"},
    },
}
EXPECTED = {
    "route": {
        "type": "choice",
        "choice": "sales",
        "confidence": 0.2,
        "probabilities": {"support": 0.2, "sales": 0.8},
    },
    "severity": {
        "type": "score",
        "score": 0.7,
        "confidence": 0.4,
        "probabilities": {"0": 0.3, "1": 0.7},
        "legend": {"0": "Low", "1": "High"},
    },
    "urgent": {"type": "boolean", "probability_true": 0.3},
}


def questions():
    return {key: Question.model_validate(value) for key, value in QUESTIONS.items()}


def decisions_settings(**patch):
    return ClassificationSettings.model_validate(
        {
            "enabled": True,
            "default_provider": DECISIONS_PROVIDER_ID,
            "providers": {DECISIONS_PROVIDER_ID: {"enabled": True, "default_model": "gpt-6-luna"}},
            **patch,
        }
    )


def test_decisions_defaults_preserve_jev_and_do_not_materialize_overlay_fields():
    defaults = ClassificationSettings()
    assert defaults.enabled is False and defaults.default_provider == "jev"
    assert defaults.providers["jev"].default_model == "jev-1.13.0"
    assert defaults.providers["jev"].api_key_env == "TYPESAFE_API_KEY"
    decisions = defaults.providers[DECISIONS_PROVIDER_ID]
    assert decisions.enabled is False and decisions.default_model == "gpt-6-luna"
    assert decisions.api_key_env == "OPENAI_API_KEY"
    overlay = ClassificationSettings.model_validate(
        {
            "providers": {DECISIONS_PROVIDER_ID: {"timeout_seconds": 12}},
            "profiles": {"pending": {"enabled": False, "provider": DECISIONS_PROVIDER_ID}},
        }
    )
    assert overlay.model_dump(mode="json", exclude_unset=True)["providers"] == {
        DECISIONS_PROVIDER_ID: {"timeout_seconds": 12.0},
    }
    assert overlay.profiles["pending"].model is None


def test_disabled_decisions_cannot_dispatch_or_advertise_runnable_profiles():
    service = ClassificationService(secret_resolver=lambda _: pytest.fail("must not read a secret"))
    settings = decisions_settings(
        providers={DECISIONS_PROVIDER_ID: {}},
        profiles={
            "pending": {
                "provider": DECISIONS_PROVIDER_ID,
                "questions": QUESTIONS,
            }
        },
    )
    payload = {"state": "sample", "questions": QUESTIONS}
    with pytest.raises(ClassificationError, match="not enabled"):
        service.prepare(settings, payload)
    prepared = PreparedEvaluation(
        state="sample",
        questions=questions(),
        provider=DECISIONS_PROVIDER_ID,
        model="gpt-6-luna",
    )
    with pytest.raises(ClassificationError, match="not enabled"):
        service.execute(settings, prepared)
    assert service.list_profiles(settings) == {"profiles": []}


@pytest.mark.parametrize(
    "answer", [{}, {"value": 1}, {"value": "false"}, {"value": None}, {"probability_true": True}]
)
def test_boolean_answers_require_an_actual_boolean_or_valid_probability(answer):
    with pytest.raises(ClassificationError):
        validate_answers(
            {"urgent": questions()["urgent"]}, {"urgent": {"type": "boolean", **answer}}
        )


def test_boolean_value_and_probability_remain_distinct_public_bindings():
    source = {"urgent": questions()["urgent"]}
    assert validate_answers(source, {"urgent": {"type": "boolean", "value": True}}) == {
        "urgent": {"type": "boolean", "value": True},
    }
    assert validate_answers(source, {"urgent": {"type": "boolean", "probability_true": 0.7}}) == {
        "urgent": {"type": "boolean", "probability_true": 0.7},
    }
    fields = answer_schema(source)["properties"]["urgent"]["properties"]
    assert fields["value"] == {"type": "boolean"}
    assert fields["probability_true"] == {"type": "number"}


def wire_response(names=("route", "severity", "urgent")):
    answers = {
        "route": {
            "name": "route",
            "type": "choice",
            "choice": "sales",
            "confidence": 0.2,
            "probabilities": [
                {"value": "support", "probability": 0.2},
                {"value": "sales", "probability": 0.8},
            ],
        },
        "severity": {
            "name": "severity",
            "type": "score",
            "score": 0.7,
            "confidence": 0.4,
            "probabilities": [
                {"value": 0, "label": "0", "probability": 0.3},
                {"value": 1, "label": "1", "probability": 0.7},
            ],
        },
        "urgent": {"name": "urgent", "type": "predicate", "probability": 0.3},
    }
    return {
        "model": "gpt-6-luna",
        "answers": [answers[name] for name in names],
        "usage": {
            "input_tokens": 12,
            "output_tokens": 0,
            "total_tokens": 12,
            "input_tokens_details": {"cached_tokens": 4, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def provider_with_calls():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json=wire_response(q["name"] for q in body["questions"]))

    return DecisionsProvider(transport=httpx.MockTransport(handler)), calls


@pytest.mark.parametrize("restored", [False, True])
@pytest.mark.parametrize("minimum, expected_port", [(0.8, "sales"), (0.81, "support")])
def test_decisions_probability_cutoff_uses_selected_probability(restored, minimum, expected_port):
    provider, calls = provider_with_calls()
    service = ClassificationService(
        providers={DECISIONS_PROVIDER_ID: provider}, secret_resolver=lambda _: "synthetic-key"
    )
    config = {
        "state": "sample",
        "instructions": "Route requests",
        "cases": [{"port": "sales", "description": "Purchases"}],
        "default_port": "support",
        "min_probability": minimum,
    }
    prepared = prepare_switch(service, decisions_settings(), config)
    if restored:
        prepared = type(prepared).model_validate_json(prepared.model_dump_json())
    result = execute_switch(service, decisions_settings(), prepared)
    assert result["selected_choice"] == "sales"
    assert result["selected_port"] == expected_port
    assert result["routing_reason"] == (
        "choice" if expected_port == "sales" else "below_probability"
    )
    assert len(calls) == 1


def test_decisions_provider_catalog_and_saves_retain_inherited_settings(environment):
    admin, configs, _ = environment
    before = admin.classification_admin.snapshot("root")
    saved = admin.classification_admin.save(
        "root",
        expected_revision=before["revision"],
        config_payload={
            "enabled": True,
            "providers": {DECISIONS_PROVIDER_ID: {"api_key_env": "SYNTHETIC_DECISIONS_KEY"}},
            "profiles": {
                "pending": {
                    "enabled": False,
                    "provider": DECISIONS_PROVIDER_ID,
                    "questions": QUESTIONS,
                }
            },
        },
    )
    pending = next(p for p in saved["providers"] if p["id"] == DECISIONS_PROVIDER_ID)
    assert pending["available"] is True and pending["unavailable_reason"] is None
    assert pending["defaults"]["default_model"] == "gpt-6-luna"
    assert pending["defaults"]["api_key_env"] == "OPENAI_API_KEY"
    assert saved["effective_config"]["profiles"]["pending"]["model"] is None
    child = admin.classification_admin.snapshot("root/team")
    child = admin.classification_admin.save(
        "root/team",
        expected_revision=child["revision"],
        config_payload={
            "providers": {DECISIONS_PROVIDER_ID: {"max_retries": 0}},
        },
    )
    assert child["config"]["providers"] == {DECISIONS_PROVIDER_ID: {"max_retries": 0}}
    settings = child["effective_config"]["providers"][DECISIONS_PROVIDER_ID]
    assert settings["api_key_env"] == "SYNTHETIC_DECISIONS_KEY"
    assert settings["enabled"] is False and settings["default_model"] == "gpt-6-luna"


@pytest.fixture
def decisions_environment(environment, monkeypatch):
    admin, configs, _ = environment
    provider, calls = provider_with_calls()
    service = admin.classification_admin.service
    service.providers[DECISIONS_PROVIDER_ID] = provider
    configs.write(
        "root",
        domain="providers",
        payload={
            "classification": {
                "enabled": True,
                "default_provider": DECISIONS_PROVIDER_ID,
                "providers": {
                    DECISIONS_PROVIDER_ID: {"enabled": True, "default_model": "gpt-6-luna"}
                },
                "profiles": {
                    "triage": {
                        "provider": DECISIONS_PROVIDER_ID,
                        "model": "gpt-6-luna",
                        "questions": QUESTIONS,
                    }
                },
            }
        },
    )
    monkeypatch.setattr(
        "evander.gates.providers.classifier_switch.service_for_runtime", lambda _: service
    )
    return admin, configs, calls


@pytest.mark.parametrize("surface", ["admin", "agent", "query", "switch"])
def test_decisions_http_adapter_uses_every_shared_classification_surface(
    decisions_environment, surface
):
    admin, configs, calls = decisions_environment
    scope = "root/team"
    payload = {"state": "A new purchase", "profile_id": "triage"}
    if surface == "admin":
        result = admin.classification_admin.evaluate(scope, payload)
    elif surface == "agent":
        admin.runtime.session_repository.upsert_session(
            SessionManifest(
                session_id="finite-agent",
                directory_path=scope,
                allowed_tools=[ToolName.CLASSIFICATION_EVALUATE],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        result = call_tool(
            create_server(admin.runtime),
            "classification_evaluate",
            {"session_id": "finite-agent", **payload},
        )
    else:
        node = (
            {
                "id": "classify",
                "display_name": "Classify",
                "type": "query:classification:evaluate",
                **payload,
            }
            if surface == "query"
            else {
                "id": "classify",
                "display_name": "Choose branch",
                "type": "gate:classifier:switch",
                "state": "A new purchase",
                "instructions": "Route requests",
                "cases": [{"port": "sales", "description": "Purchases"}],
                "default_port": "support",
            }
        )
        config = canonical_worker_config(
            {
                "runtime_dir": admin.config.runtime_dir,
                "triggers": [
                    {
                        "id": "tick",
                        "display_name": "Tick",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    }
                ],
                "queries": [node] if surface == "query" else [],
                "gates": [node] if surface == "switch" else [],
                "edges": [{"source_id": "tick", "target_id": "classify"}],
                "workflow_graphs": [
                    {
                        "id": "graph",
                        "name": "Finite decisions",
                        "directory_path": scope,
                        "node_ids": ["tick", "classify"],
                    }
                ],
            }
        )
        worker = worker_for(admin, config, pending=surface == "query")
        worker.gate_provider_factory = GateProviderFactory(admin.runtime.provider_config)
        worker.event_queue.publish(
            TriggerEvent(
                event_id="finite-input",
                trigger_id="tick",
                trigger_type="trigger:cron",
                occurred_at=datetime.now(UTC),
                payload={"message": "Original input"},
            )
        )
        worker.run_once()
        if surface == "query":
            # Queued work must keep its provider, model, and rubric across a restart.
            changed = copy.deepcopy(QUESTIONS)
            changed["severity"]["criteria"] = ["Replaced", "After enqueue"]
            configs.write(
                "root",
                domain="providers",
                payload={
                    "classification": {
                        "enabled": True,
                        "providers": {
                            DECISIONS_PROVIDER_ID: {
                                "enabled": True,
                                "default_model": "changed-model",
                            }
                        },
                        "profiles": {"triage": {"provider": "jev", "questions": changed}},
                    }
                },
            )
            worker = worker_for(admin, config)
        for _ in range(4):
            worker.run_once()
        records = (
            effect_execution_payload(config)
            if surface == "query"
            else gate_execution_payload(config)
        )
        record = next(iter(records.values()))
        assert record["status"] == "succeeded", record
        if surface == "switch":
            assert record["completion_event_branch_port"] == "sales"
            assert record["outputs"] == {"message": "Original input"}
            result = record["resolved_config"]["decision"]
        else:
            result = record["outputs"]
    assert result["provider"] == DECISIONS_PROVIDER_ID and result["model"] == "gpt-6-luna"
    assert result["answers"] == ({"route": EXPECTED["route"]} if surface == "switch" else EXPECTED)
    assert len(calls) == 1
    event = model_usage_store(admin.config.runtime_dir).list_events()[0]
    assert event.provider == "openai" and event.billing_kind == "api"
    assert event.api_key_name == "OPENAI_API_KEY" and event.input_tokens == 12
    assert event.output_tokens == 0 and event.provider_endpoint_identifier == "decisions"
    assert event.directory_path == scope
    assert event.raw_usage_metadata["classification_provider"] == DECISIONS_PROVIDER_ID
    assert "A new purchase" not in str(event.model_dump())
