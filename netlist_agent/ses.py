"""Import a Specctra session (.ses) — an external autorouter's output —
back into the board model.

Mirrors :mod:`netlist_agent.dsn`'s conventions in reverse: coordinates come
in the session's resolution units (µm unless stated otherwise) with a y-up
axis, and are converted to mm with y negated back to the board's y-down
convention. Net names map to board net codes by name (leading "/" stripped
on both sides); unknown nets import with net_code 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .kicad_pcb import (
    Board,
    SExpr,
    TrackSegment,
    Via,
    _child,
    _children,
    parse_board,
    parse_sexpr,
)

_UNIT_TO_MM = {"um": 1e-3, "mil": 0.0254, "mm": 1.0, "cm": 10.0, "inch": 25.4}


@dataclass(slots=True)
class SesRoutes:
    segments: list[TrackSegment]
    vias: list[Via]


def _plain(name: str) -> str:
    return name[1:] if name.startswith("/") else name


def _find_blocks(expr: SExpr, tag: str) -> list[SExpr]:
    """All (tag ...) blocks anywhere in the tree (SES nesting varies by tool)."""
    found: list[SExpr] = []
    if isinstance(expr, list):
        if expr and expr[0] == tag:
            found.append(expr)
        for item in expr:
            if isinstance(item, list):
                found.extend(_find_blocks(item, tag))
    return found


def parse_ses(path: Path | str, board: Board) -> SesRoutes:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    root = parse_sexpr(text)

    unit = 1e-3  # µm default, divisor 1
    for res in _find_blocks(root, "resolution"):
        # (resolution um 10) means 10 units per µm.
        if len(res) >= 2 and isinstance(res[1], str) and res[1] in _UNIT_TO_MM:
            divisor = float(res[2]) if len(res) >= 3 and isinstance(res[2], str) else 1.0
            unit = _UNIT_TO_MM[res[1]] / max(divisor, 1.0)
            break

    def to_mm(value: str) -> float:
        return round(float(value) * unit, 4)

    codes_by_name = {_plain(name): code for code, name in board.nets.items() if name}

    routes = SesRoutes(segments=[], vias=[])
    for net_expr in _find_blocks(root, "net"):
        if len(net_expr) < 2 or not isinstance(net_expr[1], str):
            continue
        net_code = codes_by_name.get(_plain(net_expr[1]), 0)

        for wire in _children(net_expr, "wire"):
            path_expr = _child(wire, "path")
            if path_expr is None or len(path_expr) < 3:
                continue
            layer = str(path_expr[1])
            width = round(float(path_expr[2]) * unit, 4)
            coords = [float(v) for v in path_expr[3:] if isinstance(v, str)]
            points = [
                (to_mm(str(coords[i])), round(-coords[i + 1] * unit, 4))
                for i in range(0, len(coords) - 1, 2)
            ]
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                routes.segments.append(
                    TrackSegment(x1=x1, y1=y1, x2=x2, y2=y2, width=width, layer=layer, net_code=net_code)
                )

        for via_expr in _children(net_expr, "via"):
            values = [v for v in via_expr[1:] if isinstance(v, str)]
            if len(values) < 3:
                continue  # padstack name plus x y
            x, y = float(values[1]), float(values[2])
            routes.vias.append(Via(x=to_mm(str(x)), y=round(-y * unit, 4), net_code=net_code))

    return routes


def _fmt(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def apply_ses(board_source: Path, ses_source: Path, output: Path) -> SesRoutes:
    """Write a copy of the board with the session's routes inserted."""
    board = parse_board(board_source)
    routes = parse_ses(ses_source, board)

    text = Path(board_source).read_text(encoding="utf-8", errors="ignore")
    close = text.rindex(")")
    lines = "".join(
        f"  (segment (start {_fmt(s.x1)} {_fmt(s.y1)}) (end {_fmt(s.x2)} {_fmt(s.y2)})"
        f' (width {_fmt(s.width)}) (layer "{s.layer}") (net {s.net_code}))\n'
        for s in routes.segments
    ) + "".join(
        f"  (via (at {_fmt(v.x)} {_fmt(v.y)}) (size 0.8) (drill 0.4)"
        f' (layers "F.Cu" "B.Cu") (net {v.net_code}))\n'
        for v in routes.vias
    )
    if close > 0 and text[close - 1] != "\n":
        lines = "\n" + lines
    Path(output).write_text(text[:close] + lines + text[close:], encoding="utf-8")
    return routes
