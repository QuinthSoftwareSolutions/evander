from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest

from config_helpers import canonical_worker_config, effect_execution_payload, gate_execution_payload
from test_classification_integration import call_tool, environment as environment, worker_for
from evander.classification.decisions import DecisionsProvider
from evander.classification.finite import finite_questions, normalize_selections
from evander.classification.models import (
    DECISIONS_PROVIDER_ID,
    ClassificationError,
    ClassificationSettings,
    PreparedEvaluation,
    Question,
    answer_schema,
    json_bytes,
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
    "route": {"type": "choice", "choice": "sales"},
    "severity": {"type": "score", "score": 1.0, "legend": {"0": "Low", "1": "High"}},
    "urgent": {"type": "boolean", "value": False},
}


def questions():
    return {key: Question.model_validate(value) for key, value in QUESTIONS.items()}


def decisions_settings(**patch):
    return ClassificationSettings.model_validate(
        {
            "enabled": True,
            "default_provider": DECISIONS_PROVIDER_ID,
            "providers": {
                DECISIONS_PROVIDER_ID: {"enabled": True, "default_model": "synthetic-model"}
            },
            **patch,
        }
    )


def test_decisions_defaults_preserve_jev_and_do_not_materialize_overlay_fields():
    defaults = ClassificationSettings()
    assert defaults.enabled is False and defaults.default_provider == "jev"
    assert defaults.providers["jev"].default_model == "jev-1.13.0"
    assert defaults.providers["jev"].api_key_env == "TYPESAFE_API_KEY"
    decisions = defaults.providers[DECISIONS_PROVIDER_ID]
    assert decisions.enabled is False and decisions.default_model is None
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


def test_unavailable_decisions_cannot_dispatch_or_advertise_runnable_profiles():
    service = ClassificationService(secret_resolver=lambda _: pytest.fail("must not read a secret"))
    settings = decisions_settings(
        profiles={
            "pending": {
                "provider": DECISIONS_PROVIDER_ID,
                "questions": QUESTIONS,
            }
        }
    )
    payload = {"state": "sample", "questions": QUESTIONS}
    with pytest.raises(ClassificationError, match="not available yet"):
        service.prepare(settings, payload)
    prepared = PreparedEvaluation(
        state="sample",
        questions=questions(),
        provider=DECISIONS_PROVIDER_ID,
        model="synthetic-model",
    )
    with pytest.raises(ClassificationError, match="not available yet"):
        service.execute(settings, prepared)
    assert service.list_profiles(settings) == {"profiles": []}


def test_finite_mapping_preserves_options_and_rubrics_without_synthetic_certainty():
    source = questions()
    finite = finite_questions(source)
    assert finite["route"].options == {"support": "Product issues", "sales": "Purchases"}
    assert finite["severity"].options == {"0": "Low", "1": "High"}
    assert finite["urgent"].options == {"true": "The proposition is true.", "false": "Can wait"}
    assert (
        normalize_selections(source, {"route": "sales", "severity": "1", "urgent": "false"})
        == EXPECTED
    )
    finite["route"].instructions["task"] = "Changed by a transport"
    finite["route"].options.clear()
    assert source["route"].instructions == {"task": "Choose a team"}
    assert set(source["route"].criteria) == {"support", "sales"}


@pytest.mark.parametrize(
    "selections",
    [
        {"route": "sales"},
        {"route": "sales", "severity": "1", "urgent": "false", "extra": "sales"},
        {"route": "unknown", "severity": "1", "urgent": "false"},
        {"route": "sales", "severity": "1.0", "urgent": "false"},
        {"route": "sales", "severity": "2", "urgent": "false"},
        {"route": "sales", "severity": "1", "urgent": "yes"},
        {"route": "sales", "severity": "1", "urgent": True},
    ],
)
def test_finite_mapping_rejects_undeclared_questions_or_answers(selections):
    with pytest.raises(ClassificationError):
        normalize_selections(questions(), selections)


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


class SyntheticDecisionsProvider(DecisionsProvider):
    """Exercise the internal adapter seam, without assuming any OpenAI HTTP schema."""

    available = True
    endpoint = "https://api.openai.com"

    def __init__(self):
        self.calls = []

    def evaluate(self, request, settings, api_key, record_attempt):
        self.calls.append(request.model_copy(deep=True))
        selections = {"route": "sales", "severity": "1", "urgent": "false"}
        response = {
            "model": request.model,
            "answers": normalize_selections(
                request.questions, {key: selections[key] for key in request.questions}
            ),
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }
        record_attempt(
            status="success",
            payload=response,
            duration_ms=1,
            request_body=json_bytes(request.model_dump(mode="json")),
            response_body=json_bytes(response),
        )
        return response


@pytest.mark.parametrize("restored", [False, True])
def test_probability_cutoff_is_rejected_before_finite_provider_call(restored):
    provider = SyntheticDecisionsProvider()
    service = ClassificationService(
        providers={DECISIONS_PROVIDER_ID: provider}, secret_resolver=lambda _: "synthetic-key"
    )
    config = {
        "state": "sample",
        "instructions": "Route requests",
        "cases": [{"port": "sales", "description": "Purchases"}],
        "default_port": "support",
    }
    if restored:
        prepared = prepare_switch(service, decisions_settings(), config)
        prepared.config.min_probability = 0.8

        def action():
            return execute_switch(service, decisions_settings(), prepared)
    else:

        def action():
            return prepare_switch(service, decisions_settings(), {**config, "min_probability": 0.8})

    with pytest.raises(ClassificationError, match="requires option probabilities"):
        action()
    assert provider.calls == []


def test_pending_provider_catalog_and_saves_retain_inherited_settings(environment):
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
    assert pending["available"] is False and pending["unavailable_reason"]
    assert pending["defaults"]["default_model"] is None
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
    assert settings["enabled"] is False and settings["default_model"] is None


@pytest.fixture
def finite_environment(environment, monkeypatch):
    admin, configs, _ = environment
    provider = SyntheticDecisionsProvider()
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
                    DECISIONS_PROVIDER_ID: {"enabled": True, "default_model": "synthetic-model"}
                },
                "profiles": {
                    "triage": {
                        "provider": DECISIONS_PROVIDER_ID,
                        "model": "synthetic-model",
                        "questions": QUESTIONS,
                    }
                },
            }
        },
    )
    monkeypatch.setattr(
        "evander.gates.providers.classifier_switch.service_for_runtime", lambda _: service
    )
    return admin, configs, provider


@pytest.mark.parametrize("surface", ["admin", "agent", "query", "switch"])
def test_finite_adapter_uses_every_shared_classification_surface(finite_environment, surface):
    admin, configs, provider = finite_environment
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
    assert result["provider"] == DECISIONS_PROVIDER_ID and result["model"] == "synthetic-model"
    assert result["answers"] == ({"route": EXPECTED["route"]} if surface == "switch" else EXPECTED)
    assert len(provider.calls) == 1
    event = model_usage_store(admin.config.runtime_dir).list_events()[0]
    assert event.provider == "openai" and event.billing_kind == "api"
    assert event.api_key_name == "OPENAI_API_KEY" and event.input_tokens == 12
    assert event.directory_path == scope
    assert event.raw_usage_metadata["classification_provider"] == DECISIONS_PROVIDER_ID
    assert "A new purchase" not in str(event.model_dump())
