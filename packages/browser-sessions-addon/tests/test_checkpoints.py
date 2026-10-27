"""Observation predicates must not turn incomplete or ambiguous state into proof."""

import pytest
from pydantic import ValidationError

from evander_browser_sessions_addon.checkpoints import evaluate
from evander_browser_sessions_addon.models import BrowserError, RunRequest


def request(condition, *, literal="  Exact value  ", **kwargs):
    return RunRequest(
        request_id="check",
        generation=1,
        goal="Set and check the field",
        values={"name": {"description": "Field value", "value": literal}},
        checkpoints=[
            {
                "id": "checked",
                "after": {"action": "fill", "input": "name"},
                "condition": condition,
                **kwargs,
            }
        ],
    )


def test_completion_schema_and_input_validation():
    payload = {"request_id": "finish", "generation": 1, "goal": "Set Name"}
    assert RunRequest(**payload).completion is None
    assert "completion" in RunRequest.model_json_schema()["required"]
    with pytest.raises(ValidationError, match="Completion input IDs"):
        RunRequest(**payload, completion={"kind": "value", "target": {"ref": "name"}, "input": "missing"})
    with pytest.raises(ValidationError, match="stop_after"):
        RunRequest(
            **payload,
            completion={"kind": "text", "text": "Saved"},
            checkpoints=[{
                "id": "saved", "after": {"action": "click", "ref": "save"},
                "condition": {"kind": "text", "text": "Saved"}, "stop_after": True,
            }],
        )


@pytest.mark.parametrize(
    "condition,element,expected",
    [
        (
            {
                "kind": "present",
                "target": {"role": "textbox", "name": "Name", "context": "Account"},
            },
            {"context": [{"role": "form", "name": "Account"}]},
            True,
        ),
        (
            {"kind": "present", "target": {"role": "textbox", "name": "Name", "context": "Other"}},
            {"context": [{"role": "form", "name": "Account"}]},
            False,
        ),
        ({"kind": "absent", "target": {"role": "alert"}}, {}, True),
        (
            {"kind": "value", "target": {"ref": "node-1"}, "input": "name"},
            {"value": "  Exact value  "},
            True,
        ),
        (
            {"kind": "value", "target": {"ref": "node-1"}, "input": "name"},
            {"value": "Exact value"},
            False,
        ),
        ({"kind": "checked", "target": {"ref": "node-1"}, "expected": False}, {}, False),
        (
            {"kind": "checked", "target": {"ref": "node-1"}, "expected": False},
            {"checked": "false"},
            True,
        ),
        (
            {"kind": "expanded", "target": {"ref": "node-1"}, "expected": True},
            {"expanded": True},
            True,
        ),
        (
            {"kind": "disabled", "target": {"ref": "node-1"}, "expected": False},
            {"disabled": 0},
            False,
        ),
        ({"kind": "text", "text": "Results loaded"}, {}, True),
    ],
)
def test_only_observed_exact_state_establishes_predicate(condition, element, expected):
    run = request(condition)
    observation = {
        "elements": [{"ref": "node-1", "role": "textbox", "name": "Name", **element}],
        "text": "Status: Results loaded",
    }
    evidence = evaluate(run.checkpoints[0].condition, observation, run.values)
    assert (evidence is not None) is expected
    if evidence is not None:
        assert not {"value", "name", "text", "context"} & evidence.keys()


@pytest.mark.parametrize("kind", ["present", "absent", "count"])
def test_incomplete_observation_cannot_establish_predicate(kind):
    run = request({"kind": kind, "target": {"role": "row"}})
    with pytest.raises(BrowserError, match="truncated"):
        evaluate(run.checkpoints[0].condition, {"elements": [], "truncated": True}, run.values)


@pytest.mark.parametrize(
    "value,complete",
    [
        ("X" * 999, True),
        ("X" * 1000, False),
        ("\U0001f600" * 499 + "X", True),
        ("\U0001f600" * 500, False),
    ],
)
def test_exact_value_evidence_requires_completeness_in_utf16_units(value, complete):
    run = request({"kind": "value", "target": {"ref": "node-1"}, "input": "name"}, literal=value)
    evidence = evaluate(
        run.checkpoints[0].condition,
        {"elements": [{"ref": "node-1", "role": "textbox", "value": value}]},
        run.values,
    )
    assert (evidence is not None) is complete


@pytest.mark.parametrize(
    "condition",
    [
        {"kind": "script", "expression": "document.body.textContent"},
        {"kind": "present", "target": {"name": "Name"}},
        {"kind": "present", "target": {"role": "textbox", "selector": "#name"}},
        {"kind": "count", "target": {"role": "row"}, "minimum": 3, "maximum": 2},
        {"kind": "count", "target": {"role": "row"}, "maximum": True},
        {"kind": "checked", "target": {"ref": "node-1"}, "expected": "false"},
        {"kind": "value", "target": {"ref": "node-1"}, "input": "unknown"},
    ],
)
def test_invalid_predicates_are_rejected_before_claiming_run(condition):
    with pytest.raises(ValidationError):
        request(condition)


def test_checkpoint_schema_defaults_and_trigger_contract():
    run = request({"kind": "present", "target": {"ref": "node-1"}})
    assert run.checkpoints[0].timeout_seconds == 5
    assert run.checkpoints[0].stop_after is False
    payload = run.model_dump(mode="json")
    payload["checkpoints"] *= 2
    with pytest.raises(ValidationError, match="unique"):
        RunRequest.model_validate(payload)
    payload["checkpoints"][1] = {**payload["checkpoints"][1], "id": "later"}
    payload["checkpoints"][0]["stop_after"] = True
    with pytest.raises(ValidationError, match="final checkpoint"):
        RunRequest.model_validate(payload)
    payload["checkpoints"][0]["stop_after"] = False
    for trigger in (
        {"action": "fill"},
        {"action": "click"},
        {"action": "click", "ref": "node-1", "input": "name"},
        {"action": "key", "ref": "node-1"},
        {"action": "fill", "input": "name", "key": "Enter"},
    ):
        payload["checkpoints"] = [{**payload["checkpoints"][0], "after": trigger}]
        with pytest.raises(ValidationError):
            RunRequest.model_validate(payload)
