import pytest

from evander.events.provider_models import TextRegexQueryDefinition
from evander.queries.providers.base import QueryRuntimeContext
from evander.queries.providers.text_regex import TextRegexQueryProvider


def execute(**config):
    provider = TextRegexQueryProvider()
    provider.initialize(
        TextRegexQueryDefinition(id="q", display_name="Text", type="query:text:regex", **config),
        QueryRuntimeContext(),
    )
    return provider.execute()


def test_captures_arbitrary_configured_command_and_groups():
    result = execute(text="/preview staging", pattern=r"/preview (?P<target>[a-z]+)")
    assert result == {"value": "/preview staging", "matched": True, "groups": {"target": "staging"}}


def test_nonmatch_is_data_unless_required():
    assert execute(text="ordinary feedback", pattern="/preview") == {
        "value": "ordinary feedback",
        "matched": False,
        "groups": {},
    }
    with pytest.raises(ValueError, match="configured pattern"):
        execute(text="ordinary feedback", pattern="/preview", require_match=True)


def test_replace_uses_configured_pattern_and_replacement():
    assert (
        execute(
            text="refs/tags/v1", pattern="^refs/tags/", operation="replace", replacement="release-"
        )["value"]
        == "release-v1"
    )
    assert execute(text="abc", pattern="-", operation="replace")["value"] == "abc"


def test_require_match_rejects_unresolved_or_non_boolean_values():
    with pytest.raises(ValueError, match="boolean"):
        execute(text="hello", pattern="world", require_match="false")


@pytest.mark.parametrize(
    "text,pattern,operation",
    [
        (32_769 * "a", "a", "fullmatch"),
        ("a", 4097 * "a", "fullmatch"),
        (123, "a", "fullmatch"),
        ("a", "a", "execute"),
    ],
)
def test_rejects_invalid_or_excessive_input(text, pattern, operation):
    with pytest.raises(ValueError):
        execute(text=text, pattern=pattern, operation=operation)
