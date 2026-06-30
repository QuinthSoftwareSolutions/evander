import pytest

from evander.gate_runtime import GateRuntimeError, filter_when_matches
from evander.workflow.contracts import validate_filter_when_expression


def test_filter_when_supports_numeric_comparison_operators() -> None:
    assert filter_when_matches({"left": 9, "greater_than": 8}) is True
    assert filter_when_matches({"left": 8, "greater_than": 8}) is False
    assert filter_when_matches({"left": 7, "less_than": 8}) is True
    assert filter_when_matches({"left": 8, "less_than": 8}) is False


def test_filter_when_supports_regex_full_match() -> None:
    assert filter_when_matches({"left": "demo7", "matches_regex": "^[a-z0-9]+$"}) is True
    assert filter_when_matches({"left": "demo-7", "matches_regex": "^[a-z0-9]+$"}) is False


def test_filter_when_numeric_comparison_rejects_non_numeric_operands() -> None:
    with pytest.raises(GateRuntimeError, match="requires numeric operands"):
        filter_when_matches({"left": "9", "greater_than": 8})

    with pytest.raises(GateRuntimeError, match="requires numeric operands"):
        filter_when_matches({"left": True, "less_than": 8})


def test_filter_when_validation_accepts_numeric_comparison_operators() -> None:
    validate_filter_when_expression({"left": "${{query.count}}", "greater_than": 8})
    validate_filter_when_expression({"left": "${{query.count}}", "less_than": 20})
    validate_filter_when_expression({"left": "${{query.name}}", "matches_regex": "^[a-z]+$"})


def test_filter_when_validation_rejects_non_numeric_comparison_values() -> None:
    with pytest.raises(ValueError, match="greater_than must be a number"):
        validate_filter_when_expression({"left": "${{query.count}}", "greater_than": "8"})

    with pytest.raises(ValueError, match="less_than must be a number"):
        validate_filter_when_expression({"left": "${{query.count}}", "less_than": True})


def test_filter_when_validation_rejects_invalid_regex() -> None:
    with pytest.raises(ValueError, match="matches_regex must be a valid regex"):
        validate_filter_when_expression({"left": "${{query.name}}", "matches_regex": "["})
