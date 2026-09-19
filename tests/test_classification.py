from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest

from evander.classification.jev import JevProvider
from evander.classification.models import (
    ClassificationError,
    ClassificationSettings,
    EvaluationRequest,
    PreparedEvaluation,
)
from evander.classification.service import ClassificationService
from evander.model_usage.runtime import model_usage_store


QUESTIONS = {
    "route": {
        "type": "choice",
        "instructions": "Choose a team",
        "criteria": {"support": "Issues", "sales": "Purchases"},
    },
    "severity": {"type": "score", "instructions": "How severe?", "criteria": ["Minor", "Blocking"]},
    "urgent": {"type": "boolean", "instructions": "Is it urgent?"},
}
RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "route": {
            "type": "choice",
            "choice": "support",
            "probabilities": {"support": 0.9, "sales": 0.1},
            "confidence": 0.6,
        },
        "severity": {
            "type": "score",
            "score": 0.7,
            "probabilities": {"0": 0.3, "1": 0.7},
            "confidence": 0.4,
        },
        "urgent": {"type": "noul", "noul": 0.8},
    },
    "usage": {"input_tokens": 120, "output_tokens": 20},
}


def settings(**kwargs):
    return ClassificationSettings.model_validate({"enabled": True, **kwargs})


def service(handler, tmp_path=None, **kwargs):
    return ClassificationService(
        tmp_path,
        providers={"jev": JevProvider(transport=httpx.MockTransport(handler), **kwargs)},
        secret_resolver=lambda _: "test-key",
    )


def test_mixed_questions_translated_and_attributed(tmp_path):
    def handler(request):
        body = json.loads(request.content)
        assert body["questions"]["urgent"]["type"] == "noul"
        assert body["questions"]["route"]["criteria"] == QUESTIONS["route"]["criteria"]
        assert body["state"] == {"message": "The export is broken"}
        assert request.url == "https://api.typesafe.ai/v1/systemone"
        return httpx.Response(200, json=RESPONSE)

    result = service(handler, tmp_path).evaluate(
        settings(),
        {"state": {"message": "The export is broken"}, "questions": QUESTIONS},
        attribution={
            "directory_path": "root/customer",
            "workflow_run_id": "run",
            "node_id": "evaluate",
            "trace_id": "trace",
        },
    )
    assert result["answers"]["urgent"] == {"type": "boolean", "probability_true": 0.8}
    assert result["answers"]["route"]["confidence"] == 0.6
    assert result["answers"]["route"]["probabilities"]["support"] == 0.9
    events = model_usage_store(tmp_path).list_events()
    assert len(events) == 1
    assert events[0].session_id is None
    assert events[0].workflow_run_id == "run"
    assert events[0].node_id == "evaluate"
    assert events[0].input_tokens == 120
    assert "The export" not in json.dumps(events[0].model_dump(mode="json"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["answers"].pop("urgent"),
        lambda r: r["answers"]["route"].update(choice="arbitrary-action"),
        lambda r: r["answers"]["route"].update(probabilities={"support": 0.1, "sales": 0.1}),
        lambda r: r["answers"]["route"].update(confidence=float("inf")),
        lambda r: r["answers"]["severity"].update(score=12),
        lambda r: r["answers"]["urgent"].update(noul=True),
        lambda r: r["answers"]["route"].pop("probabilities"),
        lambda r: r.update(usage={"input_tokens": -1}),
    ],
)
def test_invalid_provider_response_is_rejected_without_retry(mutate):
    response = copy.deepcopy(RESPONSE)
    mutate(response)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=json.dumps(response))

    with pytest.raises(ClassificationError):
        service(handler).evaluate(settings(), {"state": "input", "questions": QUESTIONS})
    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"state": "input"},
        {"state": "input", "questions": QUESTIONS, "profile_id": "triage"},
        {"state": "input", "profile_id": "triage", "model": "other"},
        {"state": "input", "profile_id": "triage", "provider": "other"},
        {"state": "input", "questions": {}},
        {"state": "input", "questions": QUESTIONS, "directory_path": "root"},
        {"state": "input", "questions": QUESTIONS, "_classification_snapshot": {}},
    ],
)
def test_public_request_rejects_invalid_sources_and_internal_fields(payload):
    with pytest.raises(ValueError):
        EvaluationRequest.model_validate(payload)


def test_profile_snapshot_survives_profile_edit():
    svc = service(lambda _: httpx.Response(200, json=RESPONSE))
    config = settings(profiles={"triage": {"model": "jev-1.13.0", "questions": QUESTIONS}})
    prepared = svc.prepare(config, {"state": "input", "profile_id": "triage"})
    persisted = prepared.model_dump_json()
    config.profiles["triage"].model = "new-model"
    config.profiles["triage"].questions.pop("urgent")
    restored = PreparedEvaluation.model_validate_json(persisted)
    result = svc.execute(config, restored)
    assert "urgent" in result["answers"]
    assert result["profile_revision"] == prepared.profile_revision
    assert svc.prepare(config, {"state": "input", "profile_id": "triage"}).model == "new-model"


def test_service_supports_non_jev_provider_and_optional_certainty():
    class FakeProvider:
        supported_question_types = frozenset({"choice"})

        def evaluate(self, request, config, key, record_attempt):
            return {"model": "fake-1", "answers": {"route": {"type": "choice", "choice": "sales"}}}

    svc = ClassificationService(
        providers={"fake": FakeProvider()}, secret_resolver=lambda _: "test"
    )
    config = settings(default_provider="fake", providers={"fake": {"default_model": "fake-1"}})
    result = svc.evaluate(config, {"state": "input", "questions": {"route": QUESTIONS["route"]}})
    assert result["answers"]["route"] == {"type": "choice", "choice": "sales"}
    with pytest.raises(ClassificationError, match="does not support"):
        svc.evaluate(config, {"state": "input", "questions": QUESTIONS})


def test_rate_limit_retry_records_each_attempt(tmp_path):
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": "0"}, text="private upstream text")
            if len(calls) == 1
            else httpx.Response(200, json=RESPONSE)
        )

    svc = service(handler, tmp_path, sleep=sleeps.append)
    svc.evaluate(settings(), {"state": "input", "questions": QUESTIONS})
    assert len(calls) == 2 and sleeps == [0]
    events = model_usage_store(tmp_path).list_events()
    assert len(events) == 2
    assert sorted(e.request_status for e in events) == ["failed", "success"]
    assert "private upstream text" not in str(events)


@pytest.mark.parametrize("status,count", [(401, 1), (400, 1), (302, 1), (503, 3)])
def test_retry_policy_and_sanitized_errors(status, count):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="sensitive response body")

    with pytest.raises(ClassificationError, match=f"HTTP {status}") as exc:
        service(handler, sleep=lambda _: None).evaluate(
            settings(), {"state": "input", "questions": QUESTIONS}
        )
    assert "sensitive" not in str(exc.value)
    assert len(calls) == count


def test_disabled_missing_credentials_and_oversized_state_do_not_call_provider():
    def unexpected(_):
        pytest.fail("provider must not be called")

    svc = service(unexpected)
    with pytest.raises(ClassificationError, match="disabled"):
        svc.evaluate(ClassificationSettings(), {"state": "input", "questions": QUESTIONS})
    svc.secret_resolver = lambda _: None
    with pytest.raises(ClassificationError, match="credential"):
        svc.evaluate(settings(), {"state": "input", "questions": QUESTIONS})
    with pytest.raises(ClassificationError, match="max_request_bytes"):
        svc.prepare(
            settings(providers={"jev": {"max_request_bytes": 1024}}),
            {"state": "x" * 2000, "questions": QUESTIONS},
        )


def test_retry_after_beyond_deadline_fails_without_sleep():
    svc = service(
        lambda _: httpx.Response(429, headers={"Retry-After": "120"}),
        sleep=lambda _: pytest.fail("must not wait beyond deadline"),
    )
    with pytest.raises(ClassificationError, match="deadline"):
        svc.evaluate(settings(), {"state": "input", "questions": QUESTIONS})


def test_slow_stream_is_cancelled_by_total_deadline_and_records_attempt(tmp_path):
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"model":'
            await asyncio.sleep(10)

    svc = service(lambda _: httpx.Response(200, stream=SlowStream()), tmp_path)
    with pytest.raises(ClassificationError, match="deadline"):
        svc.evaluate(
            settings(providers={"jev": {"timeout_seconds": 0.05}}),
            {"state": "input", "questions": QUESTIONS},
        )
    events = model_usage_store(tmp_path).list_events()
    assert len(events) == 1 and events[0].request_status == "error"


def test_invalid_model_identity_does_not_break_error_or_usage_reporting(tmp_path):
    svc = service(
        lambda _: httpx.Response(200, json={**RESPONSE, "model": {"unexpected": "object"}}),
        tmp_path,
    )
    with pytest.raises(ClassificationError, match="model identity"):
        svc.evaluate(settings(), {"state": "input", "questions": QUESTIONS})
    event = model_usage_store(tmp_path).list_events()[0]
    assert event.model == "jev-1.13.0" and event.input_tokens == 120


def test_oversized_response_is_rejected():
    svc = service(lambda _: httpx.Response(200, content=b"x" * 2048))
    with pytest.raises(ClassificationError, match="max_response_bytes"):
        svc.evaluate(
            settings(providers={"jev": {"max_response_bytes": 1024}}),
            {"state": "input", "questions": QUESTIONS},
        )


def test_network_errors_retry_but_do_not_expose_request_details(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("private upstream details", request=request)

    with pytest.raises(ClassificationError, match="connection failed") as exc:
        service(handler, tmp_path, sleep=lambda _: None).evaluate(
            settings(), {"state": "input", "questions": QUESTIONS}
        )
    assert len(calls) == 3 and "private" not in str(exc.value)
    assert len(model_usage_store(tmp_path).list_events()) == 3
