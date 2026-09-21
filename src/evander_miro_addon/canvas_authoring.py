"""Authoring checks for Miro's editable canvas DSL, applied only to new writes."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .models import MiroError

AUTHORING_CHECKS = (
    "Complete the composer instructions through step=dsl before submitting SVG. "
    "If guidance fails, correct the request and retrieve it; do not fall back to generic SVG.",
    "Prefer native Mermaid diagram widgets for flowcharts, ERD and UML. Load "
    "format_name=diagramming with the matching notation first.",
    "For free-form diagrams, use attached line/path connectors with local widget IDs in "
    "data-start/data-end, data-arrow=end for forward flow, and explicit attachment sides. "
    "Top-down links normally leave bottom and enter top; left-to-right links leave right "
    "and enter left. Route deliberate return loops separately. Do not draw polygon arrowheads.",
    "Use data-content on a connector for its label instead of independent text beside a line. "
    "Reserve room for return loops and labels outside cards. Use shape data-content for centered "
    "node labels and textArea for multiline explanatory text.",
    "Select a canvas_scope covering the full composition, including connectors and labels. "
    "The empty board's initial 1000-by-1000 area does not cover a larger diagram automatically.",
    "Inspect the returned result_snapshot_id with miro_get_snapshot and read any uncovered area. "
    "Check that all nodes and connectors survived, arrow directions match the intended flow, "
    "and labels are readable. A write's applied status confirms delivery, not visual correctness. "
    "A generic SVG preview does not prove Miro rendered the same result.",
)

_CONNECTORS = {"line", "path"}
_WIDGETS = {"rect", "circle", "ellipse", "polygon", "text", "textarea", "image", "foreignobject"}


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def _is_widget(element: ET.Element) -> bool:
    return _tag(element) in _WIDGETS or (
        _tag(element) == "g" and element.get("data-type") == "html_content"
    )


def validate_connectors(svg: str) -> None:
    """Validate widget links without guessing geometry or rewriting user content.

    The caller has already validated XML and active content. Reads and retained
    snapshots deliberately skip these authoring rules so old boards stay repairable.
    """
    root = ET.fromstring(svg)
    elements = []
    pending = [root]
    while pending:
        element = pending.pop()
        elements.append(element)
        # Structured widget bodies have their own grammar. For example, HTML
        # prototypes may contain SVG icons that are not Miro connector widgets.
        if _tag(element) != "foreignobject":
            pending.extend(reversed(list(element)))
    ids: dict[str, ET.Element] = {}
    for element in elements:
        if local_id := element.get("id"):
            if local_id in ids:
                raise MiroError(
                    "invalid_canvas", "Local SVG ids must be unique for connector references"
                )
            ids[local_id] = element

    for index, element in enumerate(elements):
        if _tag(element) not in _CONNECTORS or element.get("data-deleted") == "true":
            continue
        label = element.get("id") or element.get("data-miro-id") or f"element {index}"
        for attr in ("data-start", "data-end"):
            target = ids.get(element.get(attr, ""))
            if (
                target is None
                or not _is_widget(target)
                or target.get("data-type") == "frame"
                or target.get("data-deleted") == "true"
            ):
                raise MiroError(
                    "invalid_canvas",
                    f"Connector {label}: {attr} must reference a local SVG id of a live widget "
                    "in this payload (not a frame or another connector). Generic SVG lines/paths "
                    "can disappear on import, leaving separate polygon arrowheads behind. "
                    "Use native connectors with data-arrow and attachment sides, and put labels "
                    'in data-content. Load miro_get_canvas_format(step="dsl", '
                    'format_name="diagramming", notation="free_form") for the syntax; '
                    "prefer a Mermaid widget for flowcharts.",
                )
        for attr, allowed in (
            ("data-arrow", {"start", "end", "both"}),
            ("data-start-side", {"top", "right", "bottom", "left"}),
            ("data-end-side", {"top", "right", "bottom", "left"}),
            ("data-shape", {"straight", "curved", "elbowed"}),
        ):
            if (value := element.get(attr)) is not None and value not in allowed:
                raise MiroError(
                    "invalid_canvas",
                    f"Connector {label}: {attr} must be one of {', '.join(sorted(allowed))}; "
                    "omit it to use Miro's default",
                )
