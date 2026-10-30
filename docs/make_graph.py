"""Draws docs/graph.png, the orchestration diagram, from the compiled SilverFile graph.

    python docs/make_graph.py

The nodes and edges come from ``build_graph().get_graph()``, so the picture can't drift from the
code: a node this layout doesn't place stops the script. Conditional edges (the Orchestrator's
choice, and Extraction's fan-out) are dashed; fixed edges are solid. The tax_math MCP server and
the document input are drawn as well, although they are not graph nodes. Rendered locally with
Pillow; no API key or network needed.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agents.extraction import FixtureW2Extractor  # noqa: E402
from graph import build_graph  # noqa: E402

OUT = ROOT / "docs" / "graph.png"
W, H = 2050, 1220

INK, MUTED, LINE = "#1F2328", "#57606A", "#4A5568"
STYLES = {  # fill, border
    "hub": ("#DCE8F7", "#2F5D9E"),
    "agent": ("#F5F7FA", "#4A5568"),
    "branch": ("#FFFFFF", "#4A5568"),
    "human": ("#FCEFD9", "#B7791F"),
    "terminal": ("#E6F4EA", "#2F855A"),
    "tool": ("#EEE7F7", "#6B46C1"),
}

# node: (centre x, centre y, width, height, style, title, subtitle)
LAYOUT = {
    "__start__": (210, 560, 170, 64, "terminal", "START", ""),
    "orchestrator": (760, 560, 340, 110, "hub", "Orchestrator", "routing · task ledger · loop guard"),
    "intake": (1360, 130, 330, 84, "agent", "Intake", "only agent that talks to the taxpayer"),
    "extraction": (1360, 290, 330, 84, "agent", "Document Extraction", "fans out one branch per W-2"),
    "aggregate_extractions": (1360, 450, 330, 84, "branch", "aggregate_extractions", "reducers merge in any order"),
    "eligibility_rules": (1360, 610, 330, 84, "agent", "Eligibility & Rules", "cited decision table"),
    "computation": (1360, 770, 330, 84, "agent", "Computation", "every number from a tax_math tool"),
    "review": (1360, 930, 330, 84, "agent", "Review / Audit", "re-checks; at most 3 reviews"),
    "extract_document": (1830, 370, 300, 84, "branch", "extract_document x N", "one per W-2, in parallel"),
    "human_review": (560, 900, 330, 84, "human", "human_review", "LangGraph interrupt()"),
    "finalize": (960, 900, 250, 84, "terminal", "finalize", "draft + audit record"),
    "__end__": (960, 1060, 170, 64, "terminal", "END", ""),
}
EXTRA = {  # drawn, but not graph nodes
    "tax_math": (1830, 850, 300, 84, "tool", "tax_math MCP server", "deterministic 2025 arithmetic"),
    "input": (210, 300, 330, 84, "branch", "Dashboard or terminal", "W-2 images, typed-in forms"),
}


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def box(d: ImageDraw.ImageDraw, spec) -> None:
    x, y, w, h, style, title, subtitle = spec
    fill, border = STYLES[style]
    radius = h // 2 if style == "terminal" else 14
    if title.startswith("extract_document"):  # parallel branches, drawn as a stack of cards
        for k in (2, 1):
            d.rounded_rectangle([x - w / 2 + 10 * k, y - h / 2 - 10 * k, x + w / 2 + 10 * k, y + h / 2 - 10 * k],
                                radius, fill=fill, outline=border, width=2)
    d.rounded_rectangle([x - w / 2, y - h / 2, x + w / 2, y + h / 2], radius, fill=fill, outline=border,
                        width=4 if style == "hub" else 2)
    ty = y - (14 if subtitle else 0)
    d.text((x, ty), title, font=font(28 if style == "hub" else 24), fill=INK, anchor="mm")
    if subtitle:
        d.text((x, y + 20), subtitle, font=font(17), fill=MUTED, anchor="mm")


def border_point(spec, toward: tuple[float, float]) -> tuple[float, float]:
    """Where the line from a box's centre toward a point leaves the box."""
    x, y, w, h = spec[:4]
    dx, dy = toward[0] - x, toward[1] - y
    if dx == dy == 0:
        return x, y
    scale = min((w / 2) / abs(dx) if dx else math.inf, (h / 2) / abs(dy) if dy else math.inf)
    return x + dx * scale, y + dy * scale


def arrow(d: ImageDraw.ImageDraw, a, b, *, dashed: bool, color: str = LINE, heads=("end",), width: int = 3):
    (x1, y1), (x2, y2) = a, b
    length = math.hypot(x2 - x1, y2 - y1)
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    if dashed:
        pos = 0.0
        while pos < length - 14:
            end = min(pos + 16, length - 14)
            d.line([(x1 + ux * pos, y1 + uy * pos), (x1 + ux * end, y1 + uy * end)], fill=color, width=width)
            pos += 26
    else:
        d.line([a, (x2 - ux * 12, y2 - uy * 12)], fill=color, width=width)
    for where in heads:
        tip, sign = ((x2, y2), 1) if where == "end" else ((x1, y1), -1)
        bx, by = tip[0] - sign * ux * 18, tip[1] - sign * uy * 18
        d.polygon([tip, (bx - uy * 8, by + ux * 8), (bx + uy * 8, by - ux * 8)], fill=color)


def connect(d, a_spec, b_spec, *, dashed: bool, offset: float = 0.0, **kw) -> None:
    a = border_point(a_spec, b_spec[:2])
    b = border_point(b_spec, a_spec[:2])
    if offset:  # keep an out-and-back pair of edges side by side
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        nx, ny = -(b[1] - a[1]) / length * offset, (b[0] - a[0]) / length * offset
        a, b = (a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny)
    arrow(d, a, b, dashed=dashed, **kw)


def main() -> None:
    graph = build_graph(extractor=FixtureW2Extractor(), computation_caller=lambda *a: None,
                        review_caller=lambda *a: None).get_graph()
    unplaced = set(graph.nodes) - set(LAYOUT)
    if unplaced:
        raise SystemExit(f"Add these nodes to LAYOUT in {Path(__file__).name}: {sorted(unplaced)}")

    img = Image.new("RGB", (W, H), "#FFFFFF")
    d = ImageDraw.Draw(img)
    d.text((60, 40), "SilverFile orchestration (LangGraph, hub and spoke)", font=font(34), fill=INK)

    pairs = {(e.source, e.target) for e in graph.edges}
    for e in graph.edges:
        a, b = LAYOUT[e.source], LAYOUT[e.target]
        # An out-and-back pair: the same offset on each, measured along its own direction, puts the
        # two edges on opposite sides of the centre line.
        connect(d, a, b, dashed=e.conditional, offset=9 if (e.target, e.source) in pairs else 0)
    for agent in ("computation", "review"):
        connect(d, LAYOUT[agent], EXTRA["tax_math"], dashed=False, color=STYLES["tool"][1], heads=("start", "end"))
    connect(d, EXTRA["input"], LAYOUT["orchestrator"], dashed=False, color=STYLES["terminal"][1])

    for spec in [*LAYOUT.values(), *EXTRA.values()]:
        box(d, spec)
    d.text((1830, 785), "MCP tool calls", font=font(17), fill=STYLES["tool"][1], anchor="mm")
    d.text((415, 420), "documents,", font=font(17), fill=STYLES["terminal"][1], anchor="rm")
    d.text((415, 442), "entered_documents", font=font(17), fill=STYLES["terminal"][1], anchor="rm")

    ly = 1140  # legend
    arrow(d, (60, ly), (170, ly), dashed=True)
    d.text((185, ly), "conditional: the Orchestrator (or Extraction's fan-out) chooses", font=font(19), fill=INK, anchor="lm")
    arrow(d, (860, ly), (970, ly), dashed=False)
    d.text((985, ly), "fixed: hands control back", font=font(19), fill=INK, anchor="lm")
    arrow(d, (1330, ly), (1440, ly), dashed=False, color=STYLES["tool"][1], heads=("start", "end"))
    d.text((1455, ly), "tool calls (not graph edges)", font=font(19), fill=INK, anchor="lm")

    img.save(OUT)
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(graph.nodes)} nodes, {len(graph.edges)} edges.")


if __name__ == "__main__":
    main()
