"""Panelize a board: replicate it rows x cols with spacing, for manufacturing.

Works at the text level like ``placement.write_placed_board``: the source
file's bytes are preserved verbatim and the copies are appended as freshly
serialized s-expression blocks just before the document's closing paren.
Each copy duplicates every top-level ``footprint``/``module``, ``segment``,
``arc``, ``via``, ``zone``, and Edge.Cuts ``gr_line``/``gr_rect`` block with
its coordinates translated by the copy offset. The copy pitch is the board
outline bounding box (falling back to the copper bounding box plus 1 mm when
there is no outline) plus ``gap_mm``.

v1 simplifications, acceptable for fabrication panels:

- Net codes and names are shared across copies: every copy's "GND" is the
  same net as the original's, so a tool reading the panel sees one merged
  netlist rather than rows*cols electrically separate boards.
- Footprint interiors (pads, text, shapes) use footprint-local coordinates,
  so only the footprint's own ``(at ...)`` is shifted; everything inside
  rides along unchanged. All other copied blocks hold absolute coordinates
  and have every ``{at, start, end, mid, xy}`` list shifted recursively
  (for ``at``, only the first two numbers — rotation is preserved).
- Copied footprints get a reference suffix: R1 -> R1_2, R1_3, ... with the
  copy index starting at 2 (copy 1 is the untouched original).
- A 1x1 panel with ``rail_mm=0`` adds nothing: the output is a byte-identical
  copy of the source.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .kicad_pcb import Board, SExpr, outline_bbox, parse_board, parse_sexpr

_COORD_TAGS = {"at", "start", "end", "mid", "xy"}
_ABSOLUTE_TAGS = {"segment", "arc", "via", "zone", "gr_line", "gr_rect"}
_FOOTPRINT_TAGS = {"footprint", "module"}
_FALLBACK_MARGIN = 1.0  # mm added around the copper bbox when there is no outline
_RAIL_EDGE_WIDTH = 0.1  # mm stroke width of generated rail Edge.Cuts lines
# V-cut lines are conventionally communicated on a comment/user layer rather
# than Edge.Cuts, so they don't merge with (and corrupt) the panel outline.
_VCUT_LAYER = "Cmts.User"
_TAB_STYLES = {"none", "mouse_bites", "v_cut"}


@dataclass(slots=True)
class PanelSpec:
    rows: int = 2
    cols: int = 2
    gap_mm: float = 3.0            # spacing between copies
    rail_mm: float = 5.0           # frame rails above/below the grid (0 = none)
    tabs: str = "none"             # "none" | "mouse_bites" | "v_cut"
    tab_width_mm: float = 5.0      # mouse-bite tab width
    bite_drill_mm: float = 0.5     # mouse-bite hole diameter
    bite_pitch_mm: float = 0.8     # hole center spacing


def _fmt(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _atom(token: str) -> str:
    if token == "" or any(ch in ' \t\r\n()"' for ch in token):
        escaped = token.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return token


def _serialize(expr: Any) -> str:
    """Single-line s-expression text for a parsed node.

    Atoms are quoted (with backslash escapes) when empty or when they contain
    whitespace, parens, or quotes; everything else stays bare.
    """
    if isinstance(expr, str):
        return _atom(expr)
    return "(" + " ".join(_serialize(item) for item in expr) + ")"


def _is_number(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def _shift_coord_list(expr: SExpr, dx: float, dy: float) -> SExpr:
    """Copy of an ``(at/start/end/mid/xy ...)`` list with its first two numbers shifted.

    For ``at`` this leaves a trailing rotation untouched; the other tags only
    carry two numbers anyway.
    """
    out: SExpr = [expr[0]]
    shifted = 0
    for item in expr[1:]:
        if shifted < 2 and isinstance(item, str) and _is_number(item):
            out.append(_fmt(float(item) + (dx if shifted == 0 else dy)))
            shifted += 1
        else:
            out.append(copy.deepcopy(item))
    return out


def _shift_expr(expr: Any, dx: float, dy: float) -> Any:
    """Deep copy of ``expr`` with every nested coordinate list translated."""
    if isinstance(expr, str):
        return expr
    if expr and expr[0] in _COORD_TAGS:
        return _shift_coord_list(expr, dx, dy)
    return [_shift_expr(item, dx, dy) for item in expr]


def _copy_footprint(fp: SExpr, dx: float, dy: float, suffix: str) -> SExpr:
    """Copy of a footprint translated by (dx, dy) with its reference suffixed.

    Only the footprint's own ``(at ...)`` moves: interior pads/text/shapes are
    footprint-local coordinates and ride along unchanged.
    """
    out = copy.deepcopy(fp)
    for item in out[1:]:
        if isinstance(item, list) and item and item[0] == "at":
            item[:] = _shift_coord_list(item, dx, dy)
            break
    for item in out[1:]:
        if not isinstance(item, list) or len(item) < 3:
            continue
        if (item[0] == "property" and item[1] == "Reference") or (
            item[0] == "fp_text" and item[1] == "reference"
        ):
            item[2] = str(item[2]) + suffix
    return out


def _is_edge_cuts(expr: SExpr) -> bool:
    for item in expr:
        if isinstance(item, list) and item and item[0] == "layer":
            return len(item) > 1 and item[1] == "Edge.Cuts"
    return False


def _copyable_blocks(root: SExpr) -> list[SExpr]:
    """Top-level blocks that belong to each copy, in document order."""
    blocks: list[SExpr] = []
    for item in root[1:]:
        if not isinstance(item, list) or not item or not isinstance(item[0], str):
            continue
        if item[0] in _FOOTPRINT_TAGS or item[0] in _ABSOLUTE_TAGS - {"gr_line", "gr_rect"}:
            blocks.append(item)
        elif item[0] in {"gr_line", "gr_rect"} and _is_edge_cuts(item):
            blocks.append(item)
    return blocks


def _fallback_bbox(board: Board) -> tuple[float, float, float, float]:
    """Copper bounding box plus a 1 mm margin, for boards without an outline."""
    xs: list[float] = []
    ys: list[float] = []
    for pad in board.pads:
        xs.append(pad.x)
        ys.append(pad.y)
    for seg in board.segments:
        xs += (seg.x1, seg.x2)
        ys += (seg.y1, seg.y2)
    for arc in board.arcs:
        xs += (arc.x1, arc.xm, arc.x2)
        ys += (arc.y1, arc.ym, arc.y2)
    for via in board.vias:
        xs.append(via.x)
        ys.append(via.y)
    for fp in board.footprints:
        xs.append(fp.x)
        ys.append(fp.y)
    if not xs:
        raise ValueError("board has neither an Edge.Cuts outline nor copper to size the panel from")
    return (
        min(xs) - _FALLBACK_MARGIN,
        min(ys) - _FALLBACK_MARGIN,
        max(xs) + _FALLBACK_MARGIN,
        max(ys) + _FALLBACK_MARGIN,
    )


def _rail_lines(x0: float, x1: float, y0: float, y1: float) -> list[SExpr]:
    """Four Edge.Cuts gr_line blocks tracing the rail rectangle's sides."""
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [
        [
            "gr_line",
            ["start", _fmt(ax), _fmt(ay)],
            ["end", _fmt(bx), _fmt(by)],
            ["layer", "Edge.Cuts"],
            ["width", _fmt(_RAIL_EDGE_WIDTH)],
        ]
        for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1])
    ]


def panelize(source: Path, spec: PanelSpec, output: Path) -> dict[str, Any]:
    """Replicate ``source`` into a rows x cols panel written to ``output``.

    Copy (0, 0) is the original content, untouched; every other copy (r, c)
    is appended with offset (c * pitch_x, r * pitch_y). When ``rail_mm > 0``
    two horizontal frame rails (rail_mm tall, separated from the grid by
    gap_mm) span the panel width above and below the grid, each drawn as four
    Edge.Cuts gr_lines. Returns stats: number of copies, the pitch, the panel
    bounding box (rails included), and the panel's total footprint count.
    """
    if spec.rows < 1 or spec.cols < 1:
        raise ValueError("panel needs at least one row and one column")
    text = Path(source).read_text(encoding="utf-8", errors="ignore")
    board = parse_board(source)
    bbox = outline_bbox(board) or _fallback_bbox(board)

    pitch_x = (bbox[2] - bbox[0]) + spec.gap_mm
    pitch_y = (bbox[3] - bbox[1]) + spec.gap_mm
    grid = (
        bbox[0],
        bbox[1],
        bbox[2] + (spec.cols - 1) * pitch_x,
        bbox[3] + (spec.rows - 1) * pitch_y,
    )

    blocks = _copyable_blocks(parse_sexpr(text))
    additions: list[SExpr] = []
    copy_index = 1  # the original is copy 1
    for row in range(spec.rows):
        for col in range(spec.cols):
            if row == 0 and col == 0:
                continue
            copy_index += 1
            dx, dy = col * pitch_x, row * pitch_y
            for block in blocks:
                if block[0] in _FOOTPRINT_TAGS:
                    additions.append(_copy_footprint(block, dx, dy, f"_{copy_index}"))
                else:
                    additions.append(_shift_expr(block, dx, dy))

    panel = list(grid)
    if spec.rail_mm > 0:
        panel[1] = grid[1] - spec.gap_mm - spec.rail_mm
        panel[3] = grid[3] + spec.gap_mm + spec.rail_mm
        additions.extend(_rail_lines(grid[0], grid[2], panel[1], grid[1] - spec.gap_mm))
        additions.extend(_rail_lines(grid[0], grid[2], grid[3] + spec.gap_mm, panel[3]))

    if additions:
        stripped = text.rstrip()
        if not stripped.endswith(")"):
            raise ValueError(f"{source}: not a parenthesized document")
        body = "".join(f"  {_serialize(block)}\n" for block in additions)
        text = f"{stripped[:-1].rstrip()}\n{body})\n"
    Path(output).write_text(text, encoding="utf-8")

    return {
        "copies": spec.rows * spec.cols,
        "pitch_mm": [round(pitch_x, 4), round(pitch_y, 4)],
        "panel_bbox": [round(v, 4) for v in panel],
        "references": len(board.footprints) * spec.rows * spec.cols,
    }
