from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from test_decisions import EXPECTED, QUESTIONS, decisions_settings, wire_response
from evander.classification.decisions import DecisionsProvider
from evander.classification.models import DECISIONS_PROVIDER_ID, ClassificationError
from evander.classification.service import ClassificationService
from evander.model_usage.runtime import model_usage_store


def service(handler, runtime_dir=None, **kwargs):
    return ClassificationService(
        runtime_dir,
        providers={
            DECISIONS_PROVIDER_ID: DecisionsProvider(
                transport=httpx.MockTransport(handler), **kwargs
            )
        },
        secret_resolver=lambda _: "test-decisions-key",
    )


def test_documented_request_and_native_answers_are_used_and_attributed(tmp_path):
    def handler(request):
        assert request.method == "POST"
        assert str(request.url) == "https://api.openai.com/v1/decisions"
        assert request.headers["Authorization"] == "Bearer test-decisions-key"
        assert request.headers["Content-Type"] == "application/json"
        assert json.loads(request.content) == {
            "model": "gpt-6-luna",
            "input": '{"message":"A new purchase"}',
            "questions": [
                {
                    "name": "route",
                    "type": "choice",
                    "instructions": '{"task":"Choose a team"}',
                    "choices": [
                        {"value": "support", "description": "Product issues"},
                        {"value": "sales", "description": "Purchases"},
                    ],
                },
                {
                    "name": "severity",
                    "type": "score",
                    "instructions": "Impact?",
                    "levels": [
                        {"label": "0", "description": "Low"},
                        {"label": "1", "description": "High"},
                    ],
                },
                {
                    "name": "urgent",
                    "type": "predicate",
                    "instructions": 'Urgent?\nBoolean criteria: {"false":"Can wait"}',
                },
            ],
        }
        payload = wire_response()
        payload["usage"]["private_input"] = "A new purchase"
        payload["usage"]["input_tokens_details"]["private_input"] = "A new purchase"
        return httpx.Response(200, json=payload)

    result = service(handler, tmp_path).evaluate(
        decisions_settings(),
        {"state": {"message": "A new purchase"}, "questions": QUESTIONS},
        attribution={
            "directory_path": "root/team",
            "workflow_run_id": "run",
            "node_id": "classify",
        },
    )
    assert result["answers"] == EXPECTED
    assert result["usage"] == {
        "input_tokens": 12,
        "output_tokens": 0,
        "total_tokens": 12,
        "input_tokens_details": {"cached_tokens": 4, "cache_write_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    }
    event = model_usage_store(tmp_path).list_events()[0]
    assert event.provider == "openai" and event.provider_endpoint_identifier == "decisions"
    assert event.api_key_name == "OPENAI_API_KEY" and event.billing_kind == "api"
    assert event.input_tokens == 12 and event.output_tokens == 0 and event.total_tokens == 12
    assert event.raw_usage_metadata["usage"]["input_tokens_details"]["cached_tokens"] == 4
    assert event.directory_path == "root/team" and event.workflow_run_id == "run"
    assert "A new purchase" not in str(event.model_dump())
    assert "test-decisions-key" not in str(event.model_dump())


@pytest.mark.parametrize(
    "state, expected_input",
    [
        ("Åtgärd\nPlease help", "Åtgärd\nPlease help"),
        (
            [{"role": "assistant", "content": "quoted conversation"}],
            '[{"content":"quoted conversation","role":"assistant"}]',
        ),
    ],
)
def test_public_state_is_text_evidence_and_never_forwarded_as_non_user_messages(
    state, expected_input
):
    def handler(request):
        assert json.loads(request.content)["input"] == expected_input
        return httpx.Response(200, json=wire_response())

    assert (
        service(handler).evaluate(decisions_settings(), {"state": state, "questions": QUESTIONS})[
            "answers"
        ]
        == EXPECTED
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(answers={}),
        lambda r: r["answers"].pop(),
        lambda r: r["answers"].reverse(),
        lambda r: r["answers"][1].update(name="route"),
        lambda r: r["answers"][0].update(name=None),
        lambda r: r["answers"][0].update(type="predicate"),
        lambda r: r["answers"].__setitem__(1, {"type": "refusal", "name": "severity"}),
        lambda r: r["answers"][0].update(choice=True),
        lambda r: r["answers"][0].update(choice="undeclared"),
        lambda r: r["answers"][0].pop("confidence"),
        lambda r: r["answers"][0].update(confidence=float("nan")),
        lambda r: r["answers"][0].update(probabilities=[]),
        lambda r: r["answers"][0]["probabilities"][1].update(value="support"),
        lambda r: r["answers"][0]["probabilities"][1].update(value=False),
        lambda r: r["answers"][0]["probabilities"][1].update(probability=-0.1),
        lambda r: r["answers"][0]["probabilities"][1].update(probability=0.7),
        lambda r: r["answers"][1]["probabilities"][0].update(value=True),
        lambda r: r["answers"][1]["probabilities"][0].update(label="wrong level"),
        lambda r: r["answers"][1].update(score=4),
        lambda r: r["answers"][1].update(score=1),
        lambda r: r["answers"][2].update(probability=True),
        lambda r: r["answers"][2].update(probability=float("inf")),
        lambda r: r.update(model={"private": "bad model"}),
        lambda r: r.update(usage={"input_tokens": 12, "output_tokens": True}),
        lambda r: r["usage"].update(input_tokens=-1),
        lambda r: r["usage"]["input_tokens_details"].update(cached_tokens=-1),
    ],
)
def test_invalid_or_refused_answers_fail_without_retry_or_workflow_result(mutate, tmp_path):
    payload, calls = wire_response(), []
    mutate(payload)

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=json.dumps(payload))

    with pytest.raises(ClassificationError):
        service(handler, tmp_path).evaluate(
            decisions_settings(), {"state": "Private evidence", "questions": QUESTIONS}
        )
    assert len(calls) == 1
    event = model_usage_store(tmp_path).list_events()[0]
    assert event.request_status == "error" and event.model == "gpt-6-luna"
    assert "Private evidence" not in str(event.model_dump())


@pytest.mark.parametrize("status, attempts", [(400, 1), (401, 1), (302, 1), (429, 3), (503, 3)])
def test_http_retries_are_bounded_and_error_bodies_are_sanitized(status, attempts, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="Private upstream details", headers={"Retry-After": "0"})

    with pytest.raises(
        ClassificationError, match=f"OpenAI Decisions returned HTTP {status}"
    ) as exc:
        service(handler, tmp_path, sleep=lambda _: None).evaluate(
            decisions_settings(), {"state": "sample", "questions": QUESTIONS}
        )
    assert "Private" not in str(exc.value)
    assert len(calls) == attempts
    events = model_usage_store(tmp_path).list_events()
    assert len(events) == attempts and all(event.request_status == "failed" for event in events)
    assert all(event.normalized_usage_status == "usage_unavailable" for event in events)


def test_rate_limit_retry_uses_retry_after_and_records_native_success_usage(tmp_path):
    calls, delays = [], []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": "0"})
            if len(calls) == 1
            else httpx.Response(200, json=wire_response())
        )

    result = service(handler, tmp_path, sleep=delays.append).evaluate(
        decisions_settings(), {"state": "sample", "questions": QUESTIONS}
    )
    assert result["answers"] == EXPECTED and len(calls) == 2 and delays == [0]
    events = model_usage_store(tmp_path).list_events()
    assert sorted(event.request_status for event in events) == ["failed", "success"]


def test_privacy_revocation_prevents_the_retry_from_dispatching():
    calls, checks = [], []

    class Guard:
        def require(self, **dispatch):
            checks.append(dispatch)
            assert dispatch["provider"] == DECISIONS_PROVIDER_ID
            assert dispatch["endpoint"] == "https://api.openai.com/v1/decisions"
            if len(checks) == 3:
                raise ClassificationError("Provider dispatch revoked")

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"})

    svc = service(handler, sleep=lambda _: None)
    svc.privacy_guard = Guard()
    with pytest.raises(ClassificationError, match="revoked"):
        svc.evaluate(decisions_settings(), {"state": "sample", "questions": QUESTIONS})
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["unsupported_model", "expanded_request", "credential"])
def test_invalid_configuration_and_expanded_payloads_do_not_dispatch(failure):
    svc = service(lambda _: pytest.fail("must not dispatch"))
    settings = decisions_settings(
        providers={
            DECISIONS_PROVIDER_ID: {
                "enabled": True,
                "max_request_bytes": 1024,
            }
        }
    )
    definition = {"state": "sample", "questions": QUESTIONS}
    if failure == "unsupported_model":
        definition["model"] = "jev-1.13.0"
    elif failure == "expanded_request":
        definition["questions"] = {
            "severity": {
                "type": "score",
                "instructions": "Impact?",
                "criteria": ["Level"] * 40,
            }
        }
    else:
        svc.secret_resolver = lambda _: None
    prepared = svc.prepare(settings, definition)
    with pytest.raises(ClassificationError):
        svc.execute(settings, prepared)


def test_slow_response_is_bounded_by_total_deadline_and_attempt_is_recorded(tmp_path):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"model":'
            await asyncio.sleep(10)

    svc = service(lambda _: httpx.Response(200, stream=Stream()), tmp_path)
    with pytest.raises(ClassificationError, match="deadline"):
        svc.evaluate(
            decisions_settings(
                providers={
                    DECISIONS_PROVIDER_ID: {
                        "enabled": True,
                        "timeout_seconds": 0.05,
                    }
                }
            ),
            {"state": "sample", "questions": QUESTIONS},
        )
    events = model_usage_store(tmp_path).list_events()
    assert len(events) == 1 and events[0].request_status == "error"


@pytest.mark.parametrize(
    "content, match", [(b"x" * 2048, "max_response_bytes"), (b"invalid json", "invalid JSON")]
)
def test_oversized_and_invalid_json_responses_fail_once(content, match):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=content)

    with pytest.raises(ClassificationError, match=match):
        service(handler).evaluate(
            decisions_settings(
                providers={
                    DECISIONS_PROVIDER_ID: {
                        "enabled": True,
                        "max_response_bytes": 1024,
                    }
                }
            ),
            {"state": "sample", "questions": QUESTIONS},
        )
    assert len(calls) == 1
