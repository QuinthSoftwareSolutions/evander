"""Bounded layout diagnostics from Miro readback, not a reproduction of its renderer."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from io import BytesIO
import math
import re
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

MAX_ITEMS = 500
MAX_WARNINGS = 50
LIMITATIONS = [
    "This is a reconstructed layout, not a Miro screenshot or visual approval.",
    "Miro readback can contain placeholder connector coordinates. Routes are reconstructed "
    "from widget bounds and explicit attachment sides; actual elbow/curve routing is unavailable.",
    "Crossing warnings use bounding boxes and estimated aligned routes. Unresolved endpoints, "
    "rotation and unsupported geometry are reported as unreviewed; absence of warnings is not a pass.",
    "Only this snapshot selection is assessed. Labels, clipping, styling and items outside it "
    "still need visual review. Dashed preview links show relationships with unknown routing.",
]
NEXT_STEPS = [
    "View this snapshot with miro_get_snapshot(include_preview=true). Inspect any available "
    "actual Miro screenshot as well; do not describe this reconstruction as the Miro rendering.",
    "Separate decision branches: keep the main path below, route a bypass from another side "
    "through an empty side lane, and enter the later destination from that lane. Move nodes "
    "to make room; do not route a bypass through the skipped node or add fake endpoint anchors.",
    "Read current board state, preserve human edits, and update only affected native items. "
    "Use a new operation key for each changed repair; reuse a key only for an identical retry.",
    "Read and inspect each repair's result_snapshot_id again. Repeat until the issues are "
    "resolved or report what remains; stop if a write is uncertain. An applied write is not "
    "layout approval. Keep the review bounded (normally up to three repair passes).",
]


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def _number(value: str | None) -> float:
    if value is None:
        raise ValueError("Missing geometry")
    result = float(value)
    if not math.isfinite(result) or abs(result) > 10_000_000:
        raise ValueError("Unbounded geometry")
    return result


def _text(value: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", value[:2000])).split())[:160]


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    width: float
    height: float

    def anchor(self, side: str) -> tuple[float, float]:
        return {
            "left": (self.x, self.y + self.height / 2),
            "right": (self.x + self.width, self.y + self.height / 2),
            "top": (self.x + self.width / 2, self.y),
            "bottom": (self.x + self.width / 2, self.y + self.height),
        }[side]

    def contains_center(self, other: Box) -> bool:
        return (
            self.x < other.x + other.width / 2 < self.x + self.width
            and self.y < other.y + other.height / 2 < self.y + self.height
        )


@dataclass
class Node:
    id: str
    label: str
    box: Box
    shape: str
    font_size: float


@dataclass
class Edge:
    id: str
    label: str
    start: Node
    end: Node
    start_side: str
    end_side: str
    routing: str
    arrow: str

    @property
    def points(self):
        return self.start.box.anchor(self.start_side), self.end.box.anchor(self.end_side)

    @property
    def assessable(self):
        if self.start is self.end or math.dist(*self.points) < 0.01:
            return False
        if self.routing == "straight":
            return True
        if self.routing != "elbowed":
            return False
        (x1, y1), (x2, y2) = self.points
        return (
            abs(x1 - x2) < 0.01
            and (
                (self.start_side, self.end_side) == ("bottom", "top")
                and y2 > y1
                or (self.start_side, self.end_side) == ("top", "bottom")
                and y2 < y1
            )
        ) or (
            abs(y1 - y2) < 0.01
            and (
                (self.start_side, self.end_side) == ("right", "left")
                and x2 > x1
                or (self.start_side, self.end_side) == ("left", "right")
                and x2 < x1
            )
        )


def _box(element: ET.Element, dx: float, dy: float) -> Box:
    tag = _tag(element)
    if tag in {"circle", "ellipse"}:
        rx = _number(element.get("r") if tag == "circle" else element.get("rx"))
        ry = _number(element.get("r") if tag == "circle" else element.get("ry"))
        x, y = _number(element.get("cx", "0")) - rx, _number(element.get("cy", "0")) - ry
        width, height = 2 * rx, 2 * ry
    elif tag == "polygon":
        coords = [_number(v) for v in re.split(r"[\s,]+", element.get("points", "").strip())]
        if len(coords) < 6 or len(coords) % 2:
            raise ValueError("Missing polygon points")
        x, y = min(coords[::2]), min(coords[1::2])
        width, height = max(coords[::2]) - x, max(coords[1::2]) - y
    else:
        x, y = _number(element.get("x", "0")), _number(element.get("y", "0"))
        width, height = _number(element.get("width")), _number(element.get("height"))
    if min(width, height) <= 0:
        raise ValueError("Empty bounds")
    return Box(x + dx, y + dy, width, height)


def _scene(svg: str):
    nodes, refs, pending_edges, skipped = [], {}, [], []
    pending = [(ET.fromstring(svg), 0.0, 0.0, False)]
    count, truncated = 0, False
    while pending:
        element, dx, dy, unsupported = pending.pop()
        tag = _tag(element)
        identity = element.get("data-miro-id") or element.get("id")
        widget = (
            tag
            in {
                "line",
                "path",
                "rect",
                "circle",
                "ellipse",
                "polygon",
                "text",
                "textarea",
                "image",
                "foreignobject",
            }
            or (tag == "g" and element.get("data-type") == "html_content")
        ) and element.get("data-type") != "frame"
        # Frame background rectangles have no item identity. Content inside HTML
        # and diagram widgets has a different grammar and is not board geometry.
        frame = element.get("data-type") == "frame" or (
            tag == "g" and "data-frame" in element.attrib
        )
        identified = bool(identity and tag != "svg" and not frame)
        if identified:
            count += 1
            if count > MAX_ITEMS:
                truncated = True
                break
        try:
            if transform := element.get("transform"):
                match = re.fullmatch(
                    r"\s*translate\(\s*([^,\s)]+)(?:[\s,]+([^\s)]+))?\s*\)\s*", transform
                )
                if not match:
                    raise ValueError("Unsupported transform")
                dx += _number(match[1])
                dy += _number(match[2] or "0")
            if _number(element.get("data-rotation", "0")) != 0:
                raise ValueError("Rotation")
        except (ValueError, TypeError):
            unsupported = True
        deleted = element.get("data-deleted") == "true"
        if identified and not widget and not deleted:
            skipped.append(identity)
        if widget and identity and not deleted:
            try:
                if unsupported:
                    raise ValueError("Unsupported geometry")
                if tag in {"line", "path"}:
                    pending_edges.append(element)
                else:
                    node = Node(
                        identity,
                        _text(
                            element.get("data-content")
                            or element.get("data-title")
                            or ("".join(element.itertext()) if tag in {"text", "textarea"} else "")
                        ),
                        _box(element, dx, dy),
                        element.get("data-shape", tag),
                        _number(element.get("data-font-size", "18")),
                    )
                    nodes.append(node)
                    refs[identity] = node
                    if local_id := element.get("id"):
                        refs[local_id] = node
            except (ValueError, TypeError):
                skipped.append(identity)
        if not deleted and tag != "foreignobject" and element.get("data-type") != "html_content":
            pending.extend((child, dx, dy, unsupported) for child in reversed(list(element)))
    edges = []
    for element in pending_edges:
        identity = element.get("data-miro-id") or element.get("id")
        start, end = refs.get(element.get("data-start")), refs.get(element.get("data-end"))
        first, last = element.get("data-start-side"), element.get("data-end-side")
        if (
            start is None
            or end is None
            or first not in {"top", "right", "bottom", "left"}
            or last not in {"top", "right", "bottom", "left"}
        ):
            skipped.append(identity)
            continue
        edges.append(
            Edge(
                identity,
                _text(element.get("data-content", "")),
                start,
                end,
                first,
                last,
                element.get("data-shape", "unknown"),
                element.get("data-arrow", ""),
            )
        )
    return nodes, edges, skipped, truncated


def _crosses(edge: Edge, box: Box) -> bool:
    """Intersect the segment with the open box interior, excluding boundary touches."""
    start, end = edge.points
    low, high = 0.0, 1.0
    for a, b, lower, upper in zip(
        start, end, (box.x + 0.1, box.y + 0.1), (box.x + box.width - 0.1, box.y + box.height - 0.1)
    ):
        if abs(b - a) < 1e-9:
            if not lower < a < upper:
                return False
        else:
            t1, t2 = (lower - a) / (b - a), (upper - a) / (b - a)
            low, high = max(low, min(t1, t2)), min(high, max(t1, t2))
    return high - low > 1e-8


def _overlap(first: Edge, second: Edge) -> bool:
    a, b = first.points
    c, d = second.points
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1:
        return False
    if any(abs(dx * (p[1] - a[1]) - dy * (p[0] - a[0])) / length > 0.5 for p in (c, d)):
        return False
    projections = [((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length for p in (c, d)]
    return min(length, max(projections)) - max(0, min(projections)) > 8


def review_layout(svg: str) -> dict:
    nodes, edges, skipped, truncated = _scene(svg)
    warnings = []

    def warn(code, ids, message):
        if len(warnings) < MAX_WARNINGS:
            warnings.append({"code": code, "item_ids": ids, "message": message})

    for index, edge in enumerate(edges):
        for other in edges[index + 1 :]:
            if (
                edge.start is other.start
                and edge.start_side == other.start_side
                and edge.end is not other.end
            ):
                warn(
                    "shared_exit",
                    [edge.id, other.id, edge.start.id],
                    "Branches to different nodes share an exit anchor. Use distinct exit sides and reserve a bypass lane.",
                )
            if edge.assessable and other.assessable and _overlap(edge, other):
                warn(
                    "overlapping_connectors",
                    [edge.id, other.id],
                    "Reconstructed connector routes overlap. Separate the branches and their labels.",
                )
            if len(warnings) == MAX_WARNINGS:
                break
        if edge.assessable:
            for node in nodes:
                if (
                    node is edge.start
                    or node is edge.end
                    or node.box.contains_center(edge.start.box)
                    or node.box.contains_center(edge.end.box)
                ):
                    continue  # Ignore endpoint widgets and containing panels.
                if _crosses(edge, node.box):
                    warn(
                        "potential_node_crossing",
                        [edge.id, node.id],
                        "The reconstructed route intersects another item's bounds. Reroute through empty space or reposition nodes, then inspect readback.",
                    )
                if len(warnings) == MAX_WARNINGS:
                    break
        if len(warnings) == MAX_WARNINGS:
            truncated = True
            break
    unknown_routes = [edge.id for edge in edges if not edge.assessable]
    return {
        "status": "needs_review"
        if warnings
        else "partial"
        if skipped or truncated or unknown_routes
        else "no_detected_issues",
        "visual_verified": False,
        "preview_kind": "reconstructed_layout",
        "warnings": warnings,
        "coverage": {
            "node_count": len(nodes),
            "connector_count": len(edges),
            "unreviewed_item_ids": skipped[:MAX_WARNINGS],
            "unreviewed_item_count": len(skipped),
            "unknown_route_ids": unknown_routes[:MAX_WARNINGS],
            "unknown_route_count": len(unknown_routes),
            "truncated": truncated,
        },
        "limitations": LIMITATIONS,
        "next_steps": NEXT_STEPS,
    }


def render_layout_preview(svg: str) -> bytes:
    """Draw only parsed primitives. Never load board URLs, fonts, scripts or images."""
    nodes, edges, skipped, truncated = _scene(svg)
    image = Image.new("RGB", (1600, 1100), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=14)
    draw.text((20, 12), "Reconstructed layout - NOT a Miro screenshot", fill="#9b1c1c", font=font)
    draw.text(
        (20, 34),
        "Red links: reconstructed routes. Dashed links: routing unknown. Inspect warnings and coverage.",
        fill="#555555",
        font=font,
    )
    if not nodes:
        draw.text(
            (20, 75),
            "No supported item geometry in this selection. Read a populated area or inspect Miro directly.",
            fill="black",
            font=font,
        )
    else:
        left, top = min(n.box.x for n in nodes), min(n.box.y for n in nodes)
        width = max(n.box.x + n.box.width for n in nodes) - left
        height = max(n.box.y + n.box.height for n in nodes) - top
        scale = min(1540 / max(width, 1), 970 / max(height, 1), 2)

        def point(xy):
            return 30 + (xy[0] - left) * scale, 75 + (xy[1] - top) * scale

        for node in nodes:
            box = node.box
            x, y = point((box.x, box.y))
            x2, y2 = point((box.x + box.width, box.y + box.height))
            if node.shape == "rhombus":
                draw.polygon(
                    [((x + x2) / 2, y), (x2, (y + y2) / 2), ((x + x2) / 2, y2), (x, (y + y2) / 2)],
                    fill="#fff0ac",
                    outline="#766b21",
                    width=2,
                )
            elif node.shape in {"circle", "ellipse"}:
                draw.ellipse((x, y, x2, y2), fill="#e8f2ff", outline="#334155", width=2)
            elif node.shape not in {"text", "textarea"}:
                draw.rectangle((x, y, x2, y2), fill="#e8f2ff", outline="#334155", width=2)
            label_size = max(8, min(18, round(node.font_size * scale)))
            label_font = ImageFont.load_default(size=label_size)
            label_width = max(10, (x2 - x) * (0.65 if node.shape == "rhombus" else 1) - 12)
            words = node.label.split()
            lines, line = [], ""
            for word in words:
                if line and draw.textlength(line + " " + word, font=label_font) > label_width:
                    lines.append(line)
                    line = word
                else:
                    line = (line + " " + word).strip()
            lines.append(line)
            lines = lines[: max(1, min(6, int((y2 - y) / (label_size + 2))))]
            label_top = (y + y2 - len(lines) * (label_size + 2)) / 2
            for offset, line in enumerate(lines):
                while line and draw.textlength(line, font=label_font) > label_width:
                    line = line[:-1]
                label_left = (x + x2 - draw.textlength(line, font=label_font)) / 2
                draw.text(
                    (label_left, label_top + offset * (label_size + 2)),
                    line,
                    font=label_font,
                    fill="#0f172a",
                )
        for edge in edges:
            a, b = map(point, edge.points)
            color = "#c52222" if edge.assessable else "#64748b"
            if edge.assessable:
                draw.line([a, b], fill=color, width=3)
            else:
                for step in range(0, 40, 2):
                    draw.line(
                        [
                            (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                            for t in (step / 40, (step + 1) / 40)
                        ],
                        fill=color,
                        width=2,
                    )
            for tail, tip, enabled in (
                (a, b, edge.arrow in {"end", "both"}),
                (b, a, edge.arrow in {"start", "both"}),
            ):
                length = math.dist(tail, tip)
                if enabled and length > 1:
                    ux, uy = (tip[0] - tail[0]) / length, (tip[1] - tail[1]) / length
                    draw.polygon(
                        [
                            tip,
                            (tip[0] - 10 * ux + 4 * uy, tip[1] - 10 * uy - 4 * ux),
                            (tip[0] - 10 * ux - 4 * uy, tip[1] - 10 * uy + 4 * ux),
                        ],
                        fill=color,
                    )
            if edge.label:
                draw.text(
                    ((a[0] + b[0]) / 2 + 5, (a[1] + b[1]) / 2),
                    edge.label[:40],
                    fill=color,
                    font=font,
                )
        draw.text(
            (20, 1070),
            f"Unreviewed items: {len(skipped)}. Truncated: {truncated}. Native shape styling and label layout are simplified.",
            fill="#555555",
            font=font,
        )
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
