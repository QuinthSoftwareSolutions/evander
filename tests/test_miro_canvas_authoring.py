from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonContext
from evander.events.models import WorkflowNodeEnvelope
from evander.models import SessionManifest
from evander_miro_addon import register_addon
from evander_miro_addon.canvas_authoring import validate_connectors
from evander_miro_addon.client import MiroClient
from evander_miro_addon.models import MiroError
from evander_miro_addon.providers import register_providers
from evander_miro_addon.service import Access, MiroService, canvas
from evander_miro_addon.state import MiroStore, digest

# Minimal reproductions of the submitted diagram: a downward arrow, a stage
# transition pointing right, and the return loop pointing left. In Miro the
# unbound line/path disappears and the polygon survives as an independent shape.
BROKEN_ARROWS = (
    '<line x1="625" y1="550" x2="625" y2="620" stroke="#344563" stroke-width="10"/>'
    '<polygon points="625,640 607,610 643,610" fill="#344563"/>',
    '<line x1="1160" y1="1080" x2="1270" y2="1080" stroke="#344563" stroke-width="10"/>'
    '<polygon points="1290,1080 1260,1062 1260,1098" fill="#344563"/>',
    '<path d="M 2880 1180 L 2735 1180 L 2735 940 L 2600 940" fill="none" stroke="#6554C0"/>'
    '<polygon points="2600,940 2630,922 2630,958" fill="#6554C0"/>'
    '<text x="2705" y="900" text-anchor="middle">loop</text>',
)
NATIVE = (
    '<svg xmlns="http://www.w3.org/2000/svg">'
    '<line id="flow" data-start="first" data-end="second" data-arrow="end" '
    'data-start-side="bottom" data-end-side="top" data-shape="elbowed" '
    'data-content="Next" x1="100" y1="80" x2="100" y2="160"/>'
    '<rect id="first" x="0" y="0" width="200" height="80" data-content="First"/>'
    '<rect id="second" x="0" y="160" width="200" height="80" data-content="Second"/>'
    "</svg>"
)
SCOPE = {"x": -100, "y": -100, "width": 500, "height": 500}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from evander_miro_addon.models import Connection

    store = MiroStore(tmp_path)
    record = store.save_connection(
        Connection(
            id="test",
            name="Test",
            directory_path="root",
            redirect_uri="https://example.test/api/addons/miro-addon/oauth/callback",
            boards=[{"board_id": "board", "writable": True}],
            allow_workflows=True,
        ),
        0,
    )
    calls = []
    opens = []
    state = {"svg": '<svg><text data-miro-id="old">Existing</text></svg>'}
    schemas = {
        "canvas_get_canvas_composer_skill": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"step": {"type": "string"}, "is_repository": {"type": "boolean"}},
        },
        "canvas_load_format_skill": {
            "type": "object",
            "additionalProperties": False,
            "required": ["format_name"],
            "properties": {
                "format_name": {"enum": ["diagramming", "presentation", "prototyping"]},
                "notation": {
                    "enum": [
                        "flowchart",
                        "entity_relationship",
                        "uml_class",
                        "uml_sequence",
                        "free_form",
                    ]
                },
                "is_repository": {"type": "boolean"},
            },
        },
        "canvas_read_as_svg": {
            "type": "object",
            "properties": {
                "miro_url": {"type": "string"},
                **{"scope_" + key: {"type": "integer"} for key in SCOPE},
            },
        },
        "canvas_create_from_svg": {
            "type": "object",
            "properties": {"miro_url": {"type": "string"}, "svg": {"type": "string"}},
        },
        "canvas_update_from_svg": {
            "type": "object",
            "properties": {"miro_url": {"type": "string"}, "svg": {"type": "string"}},
        },
    }

    class Upstream:
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            if name == "canvas_read_as_svg":
                data = {"svg": state["svg"]}
            elif name in {"canvas_create_from_svg", "canvas_update_from_svg"}:
                state["svg"] = arguments["svg"]
                data = {"svg": state["svg"]}
            else:
                data = {"skill": "Requested provider instructions", **arguments}
            return SimpleNamespace(isError=False, structuredContent=data)

    @asynccontextmanager
    async def opened(client):
        opens.append(True)
        client.schemas = schemas
        client.session = Upstream()
        yield client

    monkeypatch.setattr(MiroClient, "open", opened)
    service = MiroService(store, Access("root", "session:author", session_id="author"))
    return SimpleNamespace(
        store=store,
        record=record,
        service=service,
        calls=calls,
        opens=opens,
        state=state,
        schemas=schemas,
    )


def invoke(entry, setup, monkeypatch):
    if entry == "agent":
        session = SessionManifest(
            session_id="author",
            directory_path="root",
            allowed_tools=["miro_get_canvas_format", "miro_create_canvas", "miro_update_canvas"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

        def require_tool(sid, name):
            assert sid == session.session_id and name in session.allowed_tools
            return session

        server = FastMCP("authoring")
        register_addon().mcp_tools(
            server,
            AddonContext(
                addon_id="miro-addon",
                runtime_dir=setup.store.root.parents[1],
                addon_runtime_dir=setup.store.root,
                service=SimpleNamespace(policy=SimpleNamespace(require_tool=require_tool)),
            ),
        )

        def call(operation, arguments):
            return asyncio.run(
                server._tool_manager.call_tool(
                    "miro_" + operation, {"session_id": "author", **arguments}
                )
            )

    else:
        providers = register_providers()
        monkeypatch.setattr(
            "evander_miro_addon.providers.provider_service", lambda *a, **kw: setup.service
        )

        def call(operation, arguments):
            registry = (
                providers.query_providers
                if operation == "get_canvas_format"
                else providers.effect_providers
            )
            kind = (
                ("query" if operation == "get_canvas_format" else "effect")
                + ":miro:"
                + operation.replace("_", "-")
            )
            provider = registry[kind]()
            provider.initialize(
                WorkflowNodeEnvelope(
                    id="authoring", display_name="Authoring", type=kind, config=arguments
                ),
                SimpleNamespace(),
            )
            return provider.execute()["result"]

    return call


@pytest.mark.parametrize("entry", ["agent", "workflow"])
@pytest.mark.parametrize("format_name", ["diagramming", "diagram"])
def test_guidance_reaches_current_composer_and_notation(entry, format_name, setup, monkeypatch):
    call = invoke(entry, setup, monkeypatch)
    for step in (None, "design", "dsl", "edit"):
        arguments = {"connection_id": "test", "is_repository": True}
        if step:
            arguments["step"] = step
        if step == "dsl":
            arguments.update(format_name=format_name, notation="flowchart")
        result = call("get_canvas_format", arguments)
        assert result["format"].get("step") == step
        assert result["format"]["is_repository"] is True
        assert result["authoring_checks"]
        if step == "dsl":
            assert result["format_specific"]["format_name"] == "diagramming"
            assert result["format_specific"]["notation"] == "flowchart"
    assert len(setup.calls) == 5
    assert not setup.store.list("operation")


def test_legacy_format_name_is_preserved_when_upstream_advertises_it(setup):
    setup.schemas["canvas_load_format_skill"]["properties"]["format_name"] = {"enum": ["diagram"]}
    result = asyncio.run(
        setup.service.execute(
            "get_canvas_format", {"connection_id": "test", "format_name": "diagram"}
        )
    )
    assert result["format_specific"]["format_name"] == "diagram"


@pytest.mark.parametrize(
    "arguments",
    [
        {"format_name": "diagramming", "notation": "made_up"},
        {"format_name": "made_up"},
        {"notation": "flowchart"},
    ],
)
def test_bad_guidance_parameters_spend_no_provider_calls(arguments, setup):
    with pytest.raises(ValueError):
        asyncio.run(
            setup.service.execute("get_canvas_format", {"connection_id": "test", **arguments})
        )
    assert not setup.calls
    assert setup.store.usage(setup.record) == 0


@pytest.mark.parametrize("entry", ["agent", "workflow"])
@pytest.mark.parametrize("operation", ["create_canvas", "update_canvas"])
@pytest.mark.parametrize("body", BROKEN_ARROWS)
def test_reproduced_arrows_fail_before_contacting_miro(entry, operation, body, setup, monkeypatch):
    call = invoke(entry, setup, monkeypatch)
    arguments = {
        "connection_id": "test",
        "board_id": "board",
        "operation_key": "diagram",
        "canvas_scope": SCOPE,
        "svg": "<svg>" + body + "</svg>",
    }
    if operation == "update_canvas":
        arguments["snapshot_id"] = "not-needed-for-local-preflight"
    with pytest.raises((MiroError, ToolError), match="data-start.*local SVG id"):
        call(operation, arguments)
    assert not setup.opens
    assert not setup.calls
    assert not setup.store.list("operation")


@pytest.mark.parametrize("entry", ["agent", "workflow"])
def test_corrected_connectors_can_retry_rejected_key_and_keep_direction_and_label(
    entry, setup, monkeypatch
):
    call = invoke(entry, setup, monkeypatch)
    arguments = {
        "connection_id": "test",
        "board_id": "board",
        "operation_key": "diagram",
        "canvas_scope": SCOPE,
        "svg": "<svg>" + BROKEN_ARROWS[0] + "</svg>",
    }
    with pytest.raises((MiroError, ToolError)):
        call("create_canvas", arguments)
    arguments["svg"] = NATIVE
    result = call("create_canvas", arguments)
    assert result["status"] == "applied"
    assert 'data-arrow="end"' in setup.state["svg"]
    assert 'data-content="Next"' in setup.state["svg"]
    assert call("create_canvas", arguments) == result
    assert [name for name, _ in setup.calls].count("canvas_create_from_svg") == 1


def test_old_malformed_board_is_still_readable_and_items_can_be_deleted(setup):
    setup.state["svg"] = '<svg><line data-miro-id="bad" x1="0" y1="0" x2="0" y2="10"/></svg>'
    result = asyncio.run(
        setup.service.execute(
            "read_canvas", {"connection_id": "test", "board_id": "board", "canvas_scope": SCOPE}
        )
    )
    assert result["data"]["item_ids"] == ["bad"]
    assert setup.service.saved_snapshot(setup.record, result["snapshot_id"]) == result
    result = asyncio.run(
        setup.service.execute(
            "update_canvas",
            {
                "connection_id": "test",
                "board_id": "board",
                "canvas_scope": SCOPE,
                "snapshot_id": result["snapshot_id"],
                "operation_key": "repair",
                "allow_delete": True,
                "svg": '<svg><line data-miro-id="bad" data-deleted="true"/></svg>',
            },
        )
    )
    assert result["status"] == "applied"


@pytest.mark.parametrize("status", ["applied", "uncertain"])
def test_historical_malformed_write_retry_returns_saved_outcome(status, setup):
    # Record the 0.57.0 request with its old defaults, independently of current
    # OperationInput, then retry after the authoring validation has changed.
    request = {
        "connection_id": "test",
        "board_id": "board",
        "frame_id": None,
        "item_ids": [],
        "canvas_scope": SCOPE,
        "snapshot_id": None,
        "operation_key": "old-diagram",
        "operation_id": None,
        "svg": "<svg>" + BROKEN_ARROWS[0] + "</svg>",
        "title": None,
        "text": None,
        "comment_id": None,
        "resolved": True,
        "allow_delete": False,
        "format_name": None,
        "source_refs": [],
    }
    op_id = digest([setup.record["key"], "old-diagram"])
    setup.store.put(
        "operation",
        op_id,
        {
            "operation_id": op_id,
            "operation": "create_canvas",
            "request": request,
            "fingerprint": digest(["create_canvas", request, "session:author"]),
            "status": status,
            "board_id": "board",
            "connection_key": setup.record["key"],
            "directory_path": "root",
        },
    )
    result = asyncio.run(setup.service.execute("create_canvas", request))
    assert result["operation_id"] == op_id and result["status"] == status
    assert not setup.opens


@pytest.mark.parametrize(
    "change",
    [
        ('data-end="second"', 'data-end="missing"'),
        ('id="second"', 'id="first"'),
        ('data-end="second"', 'data-end="flow"'),
        ('id="second"', 'id="second" data-type="frame"'),
        ('id="second"', 'id="second" data-deleted="true"'),
        ('data-arrow="end"', 'data-arrow="down"'),
        ('data-start-side="bottom"', 'data-start-side="middle"'),
        ('data-shape="elbowed"', 'data-shape="polyline"'),
    ],
)
def test_invalid_connector_contract_is_actionable(change):
    with pytest.raises(MiroError):
        validate_connectors(NATIVE.replace(*change))


@pytest.mark.parametrize(
    "svg",
    [
        NATIVE,
        NATIVE.replace('data-arrow="end"', 'data-arrow="start"'),
        NATIVE.replace('data-end="second"', 'data-end="first"'),  # Deliberate loop.
        NATIVE.replace('data-content="Next"', 'data-content="Return &amp; retry"'),
        '<svg><polygon id="decision" points="0,0 100,0 50,100"/></svg>',
        '<svg><foreignObject data-type="diagram" x="0" y="0" width="400" height="400">flowchart TD\n A --&gt; B\n B --&gt;|Retry| A</foreignObject></svg>',
        # A prototype's SVG icon is structured widget content, not a canvas connector.
        '<svg><foreignObject data-type="html" id="screen"><div>'
        '<svg><line id="icon" x1="0" y1="0" x2="10" y2="10"/></svg>'
        '<svg><path id="icon" d="M 0 0 L 10 10"/></svg>'
        "</div></foreignObject></svg>",
        '<svg><g data-frame="Area" transform="translate(100,200)">'
        + NATIVE.split(">", 1)[1].removesuffix("</svg>")
        + "</g></svg>",
    ],
)
def test_native_diagrams_and_triangular_widgets_remain_valid(svg):
    validate_connectors(canvas(svg)[0])


def test_workflow_guidance_fields_and_outputs_are_exposed():
    from evander.workflow.contracts import effective_node_output_schema

    providers = register_providers()
    kind = "query:miro:get-canvas-format"
    entry = next(item for item in providers.workflow_editor_catalog_entries if item.type == kind)
    fields = {field.key: field for field in entry.fields}
    assert {"step", "notation", "format_name", "is_repository"} <= fields.keys()
    assert fields["is_repository"].kind == "boolean"
    definition = providers.node_definition_resolvers[kind](
        WorkflowNodeEnvelope(
            id="format",
            display_name="Format",
            type=kind,
            config={
                "connection_id": "test",
                "step": "dsl",
                "format_name": "diagramming",
                "notation": "flowchart",
            },
        )
    )
    assert {"format", "format_specific", "authoring_checks"} <= effective_node_output_schema(
        definition
    ).keys()
