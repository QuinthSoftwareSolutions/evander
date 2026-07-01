from __future__ import annotations

import httpx
import pytest

from evander.ado_mentions import (
    AdoMentionResolver,
    AdoResolvedMention,
    render_ado_mentions_html_prefix,
    render_ado_mentions_prefix,
)


def test_ado_mention_resolver_resolves_email_search() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "vssps.dev.azure.com"
        assert request.url.path == "/example/_apis/identities"
        assert request.url.params["searchFilter"] == "General"
        assert request.url.params["filterValue"] == "ada@example.com"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "providerDisplayName": "Ada Lovelace",
                        "uniqueName": "ada@example.com",
                        "descriptor": "aad.ada",
                        "isActive": True,
                    }
                ]
            },
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        resolver = AdoMentionResolver(
            client=client,
            organization_url="https://dev.azure.com/example",
            pat="secret",
        )
        mention = resolver.resolve("ada@example.com")

    assert mention.id == "11111111-1111-1111-1111-111111111111"
    assert mention.display_name == "Ada Lovelace"
    assert mention.unique_name == "ada@example.com"
    assert len(requests) == 1


def test_ado_mention_resolver_fails_ambiguous_search() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "providerDisplayName": "Alex",
                        "isActive": True,
                    },
                    {
                        "id": "22222222-2222-2222-2222-222222222222",
                        "providerDisplayName": "Alex",
                        "isActive": True,
                    },
                ]
            },
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        resolver = AdoMentionResolver(
            client=client,
            organization_url="https://dev.azure.com/example",
            pat="secret",
        )
        with pytest.raises(ValueError, match="ambiguous"):
            resolver.resolve("Alex")


def test_ado_mention_resolver_falls_back_from_descriptor_like_search_term() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "descriptors" in request.url.params:
            return httpx.Response(200, json={"value": []}, request=request)
        assert request.url.params["searchFilter"] == "General"
        assert request.url.params["filterValue"] == "Ada.Lovelace"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "providerDisplayName": "Ada Lovelace",
                        "uniqueName": "ada@example.com",
                        "isActive": True,
                    }
                ]
            },
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        resolver = AdoMentionResolver(
            client=client,
            organization_url="https://dev.azure.com/example",
            pat="secret",
        )
        mention = resolver.resolve("Ada.Lovelace")

    assert mention.id == "11111111-1111-1111-1111-111111111111"
    assert [request.url.params.get("descriptors") for request in requests] == [
        "Ada.Lovelace",
        None,
    ]


def test_render_ado_mentions_prefix_prepends_tokens_once() -> None:
    body = render_ado_mentions_prefix(
        "Please take a look.",
        [
            AdoResolvedMention(
                input="ada@example.com",
                id="11111111-1111-1111-1111-111111111111",
                display_name="Ada",
            ),
            AdoResolvedMention(
                input="grace@example.com",
                id="22222222-2222-2222-2222-222222222222",
                display_name="Grace",
            ),
        ],
    )

    assert body == (
        "@<11111111-1111-1111-1111-111111111111> "
        "@<22222222-2222-2222-2222-222222222222>\n\n"
        "Please take a look."
    )


def test_render_ado_mentions_html_prefix_uses_ado_mention_anchors() -> None:
    body = render_ado_mentions_html_prefix(
        "<p>Please take a look.</p>",
        [
            AdoResolvedMention(
                input="ada@example.com",
                id="11111111-1111-1111-1111-111111111111",
                display_name="Ada <Lovelace>",
            ),
            AdoResolvedMention(
                input="grace@example.com",
                id="22222222-2222-2222-2222-222222222222",
                display_name="Grace",
            ),
        ],
    )

    assert body == (
        '<p><a href="#" data-vss-mention="version:2.0,'
        '11111111-1111-1111-1111-111111111111">@Ada &lt;Lovelace&gt;</a> '
        '<a href="#" data-vss-mention="version:2.0,'
        '22222222-2222-2222-2222-222222222222">@Grace</a></p>\n'
        "<p>Please take a look.</p>"
    )
