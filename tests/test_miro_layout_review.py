from __future__ import annotations

import base64
from io import BytesIO
import json
import xml.etree.ElementTree as ET

from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult
from PIL import Image
import pytest

from evander.gateway import create_server
from evander_miro_addon.layout_review import (
    MAX_ITEMS,
    MAX_WARNINGS,
    render_layout_preview,
    review_layout,
)
from evander_miro_addon.models import MiroError
from evander_miro_addon.service import canvas
from evander_miro_addon.state import MiroStore
from test_miro_addon import read, run, update_args
from test_miro_addon import setup as setup
from test_visual_addon_discovery import add_session, call, miro_connection
from test_visual_addon_discovery import runtime as runtime


# Readback reproduces the reported geometry. Miro substitutes 0,0 -> 1,1 for
# connector coordinates; using those directly would miss the real conflict.
DECISION = '<rect id="decision" data-miro-id="decision" x="792" y="480" width="400" height="180" data-shape="rhombus" data-content="Rerun requested?"/>'
MIDDLE = '<rect id="middle" data-miro-id="middle" x="782" y="760" width="420" height="120" data-content="Select an eligible historical route run"/>'
LATER = '<rect id="later" data-miro-id="later" x="792" y="970" width="400" height="180" data-shape="rhombus" data-content="Configured route criteria match?"/>'
YES = '<line id="yes" data-miro-id="yes" data-start="decision" data-end="middle" data-start-side="bottom" data-end-side="top" data-shape="straight" data-content="Yes" data-arrow="end" x1="0" y1="0" x2="1" y2="1"/>'
NO = '<line id="no" data-miro-id="no" data-start="decision" data-end="later" data-start-side="bottom" data-end-side="top" data-shape="elbowed" data-content="No" data-arrow="end" x1="0" y1="0" x2="1" y2="1"/>'
BROKEN = "<svg>" + DECISION + MIDDLE + LATER + YES + NO + "</svg>"
FIXED_NO = NO.replace('data-start-side="bottom"', 'data-start-side="right"').replace(
    'data-end-side="top"', 'data-end-side="right"'
)
PATCH = "<svg>" + DECISION + LATER + FIXED_NO + "</svg>"


def issue_set(report):
    return {(issue["code"], tuple(issue["item_ids"])) for issue in report["warnings"]}


def test_reported_shared_branch_and_skipped_node_are_found_from_readback():
    report = review_layout(BROKEN)
    assert report["status"] == "needs_review"
    assert not report["visual_verified"]
    assert {
        ("shared_exit", ("yes", "no", "decision")),
        ("overlapping_connectors", ("yes", "no")),
        ("potential_node_crossing", ("no", "middle")),
    } <= issue_set(report)
    assert report["coverage"]["node_count"] == 3
    assert report["coverage"]["connector_count"] == 2


def test_unknown_routing_does_not_become_a_false_visual_pass():
    report = review_layout(BROKEN.replace(NO, FIXED_NO))
    assert report["warnings"] == []
    assert report["status"] == "partial"
    assert report["coverage"]["unknown_route_ids"] == ["no"]
    assert not report["visual_verified"]


def test_translated_frame_and_containing_panel_preserve_geometry_checks():
    scene = (
        '<svg><g data-frame="Routing" transform="translate(-800, 100)"><rect id="panel" x="0" y="0" width="2000" height="2000"/>'
        + BROKEN.removeprefix("<svg>").removesuffix("</svg>")
        + "</g></svg>"
    )
    assert issue_set(review_layout(scene)) == issue_set(review_layout(BROKEN))


@pytest.mark.parametrize(
    "change",
    [
        ('data-shape="straight"', 'data-shape="curved"'),
        ('data-start-side="bottom"', ""),
        ('data-end="middle"', 'data-end="outside-selection"'),
        ('width="420"', 'width="NaN"'),
        (
            'id="middle" data-miro-id="middle"',
            'id="middle" data-miro-id="middle" data-rotation="45"',
        ),
    ],
)
def test_incomplete_or_unsupported_readback_is_explicit(change):
    report = review_layout(BROKEN.replace(*change))
    coverage = report["coverage"]
    assert coverage["unreviewed_item_count"] or coverage["unknown_route_count"]
    assert not report["visual_verified"]


def test_clean_short_link_is_not_mistaken_for_a_crossing_or_visual_approval():
    report = review_layout("<svg>" + DECISION + MIDDLE + YES + "</svg>")
    assert report["status"] == "no_detected_issues"
    assert report["warnings"] == []
    assert not report["visual_verified"]


def test_structured_widget_bodies_are_not_interpreted_as_board_items():
    html = '<foreignObject id="prototype" data-miro-id="prototype" x="2000" y="0" width="400" height="400"><svg><path id="icon" d="M0,0 L1,1"/></svg></foreignObject>'
    report = review_layout(BROKEN.replace("</svg>", html + "</svg>"))
    assert report["coverage"]["node_count"] == 4
    assert report["coverage"]["unreviewed_item_count"] == 0


def test_review_and_preview_are_bounded():
    many = (
        "<svg>"
        + "".join(
            f'<rect id="n{i}" x="{i * 200}" width="100" height="100"/>'
            for i in range(MAX_ITEMS + 5)
        )
        + "</svg>"
    )
    report = review_layout(many)
    assert report["coverage"]["truncated"]
    assert report["coverage"]["node_count"] == MAX_ITEMS
    edges = "".join(YES.replace('id="yes"', f'id="edge{i}"') for i in range(20))
    crowded = "<svg>" + DECISION + MIDDLE + edges + "</svg>"
    report = review_layout(crowded)
    assert len(report["warnings"]) == MAX_WARNINGS
    assert report["coverage"]["truncated"]
    png = render_layout_preview(many)
    assert len(png) < 1_048_576
    with Image.open(BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.size == (1600, 1100)


def test_preview_handles_empty_and_nonfinite_geometry_without_loading_external_content():
    for svg in (
        "<svg/>",
        '<svg><image id="remote" href="https://example.invalid/private.png" width="100" height="100"/><rect id="bad" width="inf" height="100"/></svg>',
    ):
        with Image.open(BytesIO(render_layout_preview(svg))) as image:
            image.verify()


def test_unknown_widget_types_are_reported_as_unreviewed():
    report = review_layout(
        '<svg><g id="unknown" data-miro-id="unknown" data-type="future-widget"/></svg>'
    )
    assert report["status"] == "partial"
    assert report["coverage"]["unreviewed_item_ids"] == ["unknown"]


def test_read_repair_read_loop_preserves_snapshot_identity_and_human_edits(setup):
    store, record, remote, service = setup
    remote.svg = BROKEN
    snapshot = read(service)
    original = store.get("snapshot", snapshot["snapshot_id"])
    assert "layout_review" not in original
    assert "layout_review" not in original["data"]
    assert service.saved_snapshot(record, snapshot["snapshot_id"]) == snapshot
    assert snapshot["layout_review"]["snapshot_id"] == snapshot["snapshot_id"]
    # A concurrent human edit must still block a repair made from an old snapshot.
    remote.svg = remote.svg.replace(
        "historical route run", "historical route run reviewed by human"
    )
    with pytest.raises(MiroError, match="changed since"):
        run(
            service.execute(
                "update_canvas", update_args(snapshot, svg=PATCH, operation_key="repair-1")
            )
        )
    fresh = read(service)
    result = run(
        service.execute("update_canvas", update_args(fresh, svg=PATCH, operation_key="repair-1"))
    )
    assert result["status"] == "applied"
    assert result["layout_review"]["warnings"] == []
    assert result["layout_review"]["status"] == "partial"
    assert "reviewed by human" in remote.svg
    assert (
        service.saved_snapshot(record, result["result_snapshot_id"])["layout_review"]
        == result["layout_review"]
    )
    assert (
        run(
            service.execute(
                "update_canvas", update_args(fresh, svg=PATCH, operation_key="repair-1")
            )
        )
        == result
    )
    assert sum(op == "update_canvas" for op, _ in remote.calls) == 1


def test_creation_with_layout_warnings_is_applied_not_uncertain(setup):
    store, record, remote, service = setup
    remote.svg = "<svg/>"
    root = ET.fromstring(BROKEN)
    for element in root:
        element.attrib.pop("data-miro-id", None)
    result = run(
        service.execute(
            "create_canvas",
            {
                "connection_id": "architecture",
                "board_id": "board1",
                "operation_key": "layout",
                "canvas_scope": {"x": 0, "y": 0, "width": 1400, "height": 1400},
                "svg": ET.tostring(root, encoding="unicode"),
            },
        )
    )
    assert result["status"] == "applied"
    assert result["layout_review"]["status"] == "needs_review"
    assert result["layout_review"]["snapshot_id"] == result["result_snapshot_id"]


@pytest.mark.parametrize("include_preview", [False, True])
def test_gateway_returns_snapshot_structure_and_optional_png_under_existing_grant(
    runtime, include_preview
):
    add_session(runtime, ["miro_get_snapshot"])
    record = miro_connection(runtime, boards=[{"board_id": "board", "writable": True}])
    store = MiroStore(runtime.config.runtime_dir)
    svg, ids = canvas(BROKEN)
    snapshot = store.snapshot(record, "board", {}, "canvas", {"svg": svg, "item_ids": ids})
    server = create_server(runtime)
    result = call(
        server,
        "miro_get_snapshot",
        connection_id="studio",
        snapshot_id=snapshot["snapshot_id"],
        include_preview=include_preview,
    )
    assert isinstance(result, CallToolResult)
    assert result.structuredContent["snapshot_id"] == snapshot["snapshot_id"]
    assert result.structuredContent["layout_review"]["status"] == "needs_review"
    assert json.loads(result.content[0].text) == result.structuredContent
    if include_preview:
        assert result.content[1].mimeType == "image/png"
        with Image.open(BytesIO(base64.b64decode(result.content[1].data))) as image:
            image.verify()
    else:
        assert len(result.content) == 1
    # Exercise actual FastMCP wire conversion and the gateway audit serialization.
    tool = server._tool_manager.get_tool("miro_get_snapshot")
    assert tool.fn_metadata.convert_result(result) == result
    events = [
        json.loads(line)
        for line in (runtime.config.runtime_dir / "audit.jsonl").read_text().splitlines()
    ]
    assert any(
        event.get("tool_name") == "miro_get_snapshot" and event["decision"] == "allowed"
        for event in events
    )
    add_session(runtime, [])
    with pytest.raises(ToolError, match="not allowed"):
        call(
            server,
            "miro_get_snapshot",
            connection_id="studio",
            snapshot_id=snapshot["snapshot_id"],
            include_preview=True,
        )


def test_preview_does_not_bypass_board_or_connection_scope(runtime):
    add_session(runtime, ["miro_get_snapshot"])
    private = miro_connection(
        runtime, id="private", directory="root/sibling", boards=[{"board_id": "board"}]
    )
    miro_connection(runtime, id="visible")
    snapshot = MiroStore(runtime.config.runtime_dir).snapshot(
        private, "board", {}, "canvas", {"svg": BROKEN, "item_ids": []}
    )
    server = create_server(runtime)
    with pytest.raises(ToolError, match="unavailable|Unknown"):
        call(
            server,
            "miro_get_snapshot",
            connection_id="visible",
            snapshot_id=snapshot["snapshot_id"],
            include_preview=True,
        )
