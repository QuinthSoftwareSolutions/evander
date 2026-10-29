from __future__ import annotations

import json

from evander.relays.local_openai_tool_compatibility import (
    LocalOpenAIToolCompatibilityAdapter,
)


def _prepare(tools: list[dict[str, object]]):
    adapter = LocalOpenAIToolCompatibilityAdapter()
    compatibility = adapter.prepare_request(
        method="POST",
        upstream_path="responses",
        request_body=json.dumps({"model": "local", "tools": tools}).encode("utf-8"),
    )
    return adapter, compatibility


def test_adapter_flattens_namespace_tools_and_restores_nested_function_calls() -> None:
    adapter, compatibility = _prepare(
        [
            {"type": "function", "name": "exec_command"},
            {
                "type": "namespace",
                "name": "evander",
                "tools": [
                    {
                        "name": "get_task",
                        "description": "Get a task",
                        "parameters": {"type": "object"},
                    }
                ],
            },
        ]
    )

    rewritten_request = json.loads(compatibility.request_body)
    assert rewritten_request["tools"] == [
        {"type": "function", "name": "exec_command"},
        {
            "type": "function",
            "name": "evander__get_task",
            "description": "Get a task",
            "parameters": {"type": "object"},
        },
    ]

    response_body = json.dumps(
        {
            "output": [
                {
                    "type": "function_call",
                    "name": "mcp__evander__get_task",
                    "arguments": "{}",
                }
            ]
        }
    ).encode("utf-8")
    rewritten_response = json.loads(
        adapter.rewrite_response(response_body, compatibility=compatibility)
    )
    assert rewritten_response["output"][0]["name"] == "get_task"
    assert rewritten_response["output"][0]["namespace"] == "evander"


def test_adapter_does_not_guess_namespace_for_ambiguous_bare_function_name() -> None:
    adapter, compatibility = _prepare(
        [
            {
                "type": "namespace",
                "name": "evander",
                "tools": [{"name": "get_task"}],
            },
            {
                "type": "namespace",
                "name": "other",
                "tools": [{"name": "get_task"}],
            },
        ]
    )
    response_body = json.dumps(
        {"type": "function_call", "name": "get_task", "arguments": "{}"}
    ).encode("utf-8")

    assert adapter.rewrite_response(response_body, compatibility=compatibility) == response_body


def test_adapter_preserves_exact_top_level_function_over_namespace_guess() -> None:
    adapter, compatibility = _prepare(
        [
            {"type": "function", "name": "exec_command"},
            {
                "type": "namespace",
                "name": "evander",
                "tools": [{"name": "exec_command"}],
            },
        ]
    )
    response_body = json.dumps(
        {"type": "function_call", "name": "exec_command", "arguments": "{}"}
    ).encode("utf-8")

    assert adapter.rewrite_response(response_body, compatibility=compatibility) == response_body


def test_adapter_buffers_partial_sse_lines_and_rewrites_complete_events() -> None:
    adapter, compatibility = _prepare(
        [
            {
                "type": "namespace",
                "name": "evander",
                "tools": [{"name": "get_task"}],
            }
        ]
    )

    rewritten, remainder = adapter.rewrite_stream_chunk(
        buffer=b"",
        chunk=b'data: {"item":{"type":"function_call","name":"mcp__evander',
        compatibility=compatibility,
    )
    assert rewritten == b""

    rewritten, remainder = adapter.rewrite_stream_chunk(
        buffer=remainder,
        chunk=b'__get_task","arguments":"{}"}}\r\n\r\ndata: [DONE]\n',
        compatibility=compatibility,
    )

    assert remainder == b""
    assert rewritten.endswith(b"data: [DONE]\n")
    event_line = next(line for line in rewritten.splitlines() if line.startswith(b"data: {"))
    event = json.loads(event_line[len(b"data: ") :])
    assert event["item"]["name"] == "get_task"
    assert event["item"]["namespace"] == "evander"
