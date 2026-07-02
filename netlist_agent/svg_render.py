"""Render a board + its ratsnest to a standalone SVG for quick inspection.

Every element that belongs to a net carries a ``data-net`` attribute (the
net name, XML-escaped), so the HTML report can highlight nets interactively.
Elements also carry a ``data-layer`` attribute (the copper layer for tracks
and zones; ``via``/``pad``/``airwire`` for the rest) so the report can toggle
layer visibility. Titles are escaped too — net names come from board files.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from .kicad_pcb import Board
from .ratsnest import RatsnestReport

_LAYER_COLORS = {"F.Cu": "#c83434", "B.Cu": "#3464c8"}
_DEFAULT_TRACK_COLOR = "#888888"
_PAD_COLOR = "#d4aa00"
_AIRWIRE_COLOR = "#00e0e0"
_BACKGROUND = "#001023"
_MARGIN_MM = 2.0


def _net_attr(net_name: str) -> str:
    # Force &quot; so the value is always double-quoted, byte-identical to the
    # HTML report's html.escape(..., quote=True) encoding of the same name.
    return f' data-net={quoteattr(net_name, {chr(34): "&quot;"})}' if net_name else ""


def _layer_attr(layer: str) -> str:
    return f' data-layer={quoteattr(layer, {chr(34): "&quot;"})}' if layer else ""


def render_svg(board: Board, report: RatsnestReport, output_file: Path, scale: float = 20.0) -> None:
    xs: list[float] = []
    ys: list[float] = []
    for p in board.pads:
        xs.append(p.x)
        ys.append(p.y)
    for s in board.segments:
        xs.extend((s.x1, s.x2))
        ys.extend((s.y1, s.y2))
    for v in board.vias:
        xs.append(v.x)
        ys.append(v.y)
    for z in board.zones:
        for polygon in z.polygons:
            for x, y in polygon:
                xs.append(x)
                ys.append(y)
    if not xs:
        xs, ys = [0.0], [0.0]

    min_x, max_x = min(xs) - _MARGIN_MM, max(xs) + _MARGIN_MM
    min_y, max_y = min(ys) - _MARGIN_MM, max(ys) + _MARGIN_MM
    width = (max_x - min_x) * scale
    height = (max_y - min_y) * scale

    def sx(x: float) -> float:
        return round((x - min_x) * scale, 2)

    def sy(y: float) -> float:
        return round((y - min_y) * scale, 2)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.2f} {height:.2f}">',
        f'<rect width="100%" height="100%" fill="{_BACKGROUND}"/>',
    ]

    for z in board.zones:
        color = _LAYER_COLORS.get(z.layer, _DEFAULT_TRACK_COLOR)
        net = _net_attr(board.nets.get(z.net_code, ""))
        for polygon in z.polygons:
            pts = " ".join(f"{sx(x)},{sy(y)}" for x, y in polygon)
            parts.append(
                f'<polygon points="{pts}" fill="{color}" fill-opacity="0.25"{net}{_layer_attr(z.layer)}/>'
            )

    for s in board.segments:
        color = _LAYER_COLORS.get(s.layer, _DEFAULT_TRACK_COLOR)
        parts.append(
            f'<line x1="{sx(s.x1)}" y1="{sy(s.y1)}" x2="{sx(s.x2)}" y2="{sy(s.y2)}" '
            f'stroke="{color}" stroke-width="{max(s.width, 0.15) * scale:.2f}" stroke-linecap="round"'
            f"{_net_attr(board.nets.get(s.net_code, ''))}{_layer_attr(s.layer)}/>"
        )

    for v in board.vias:
        parts.append(
            f'<circle cx="{sx(v.x)}" cy="{sy(v.y)}" r="{0.4 * scale:.2f}" '
            f'fill="none" stroke="#b0b0b0" stroke-width="{0.15 * scale:.2f}"'
            f"{_net_attr(board.nets.get(v.net_code, ''))}{_layer_attr('via')}/>"
        )

    for p in board.pads:
        parts.append(
            f'<circle cx="{sx(p.x)}" cy="{sy(p.y)}" r="{p.radius * scale:.2f}" fill="{_PAD_COLOR}"'
            f"{_net_attr(p.net_name)}{_layer_attr('pad')}>"
            f"<title>{escape(f'{p.reference}.{p.pad_name} ({p.net_name})')}</title></circle>"
        )

    for a in report.airwires:
        parts.append(
            f'<line x1="{sx(a.x1)}" y1="{sy(a.y1)}" x2="{sx(a.x2)}" y2="{sy(a.y2)}" '
            f'stroke="{_AIRWIRE_COLOR}" stroke-width="{0.08 * scale:.2f}" '
            f'stroke-dasharray="{0.4 * scale:.1f} {0.3 * scale:.1f}"'
            f"{_net_attr(a.net_name)}{_layer_attr('airwire')}>"
            f"<title>{escape(f'{a.net_name}: {a.length:.2f} mm')}</title></line>"
        )

    parts.append("</svg>")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("\n".join(parts), encoding="utf-8")
