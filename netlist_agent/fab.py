"""Assembly-drawing SVG generator: the placement/orientation reference sheet.

Renders a print-style document (light background, unlike the dark board
render in ``svg_render``): the board outline, each footprint's courtyard
rectangle with its pads, a pin-1 marker, and the reference designator
rotated with the footprint but flipped to stay readable. Each footprint is
wrapped in a ``<g data-ref="...">`` group (quoteattr-escaped) and groups are
emitted in natural-sort reference order, so output is deterministic.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from .kicad_pcb import Board, Pad, outline_bbox
from .placement import courtyard_extents

_BACKGROUND = "#f8f9fb"
_OUTLINE_COLOR = "#333333"
_COURTYARD_STROKE = "#c0c0c0"
_COURTYARD_FILL = "#ffffff"
_PAD_COLOR = "#c8c8c8"
_TEXT_COLOR = "#222222"
_PIN1_COLOR = "#333333"
_MARGIN_MM = 2.0
_PAD_FALLBACK_MM = 0.5  # fallback courtyard: pad-center bbox grown by this much
_TEXT_SIZE_MM = 1.0
_PIN1_RADIUS_MM = 0.25


def readable_angle(angle: float) -> float:
    """Text angle normalized to [0, 360) and flipped to stay readable.

    Angles in (90, 270] would render the reference designator upside down,
    so they get 180 added; the text stays on the footprint axis either way.
    """
    normalized = angle % 360.0
    if 90.0 < normalized <= 270.0:
        normalized = (normalized + 180.0) % 360.0
    return normalized


def _ref_attr(reference: str) -> str:
    # Force &quot; so the value is always double-quoted, matching svg_render.
    return f' data-ref={quoteattr(reference, {chr(34): "&quot;"})}'


def _natural_key(text: str) -> tuple[str | int, ...]:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text))


def _copper_bbox(board: Board) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for p in board.pads:
        xs.append(p.x)
        ys.append(p.y)
    for s in board.segments:
        xs.extend((s.x1, s.x2))
        ys.extend((s.y1, s.y2))
    for a in board.arcs:
        xs.extend((a.x1, a.xm, a.x2))
        ys.extend((a.y1, a.ym, a.y2))
    for v in board.vias:
        xs.append(v.x)
        ys.append(v.y)
    if not xs:
        xs, ys = [0.0], [0.0]
    return min(xs), min(ys), max(xs), max(ys)


def render_assembly_svg(board: Board, scale: float = 20.0) -> str:
    """Assembly drawing for the board as a standalone SVG string."""
    outline = outline_bbox(board)
    bounds = outline if outline is not None else _copper_bbox(board)
    min_x, min_y = bounds[0] - _MARGIN_MM, bounds[1] - _MARGIN_MM
    max_x, max_y = bounds[2] + _MARGIN_MM, bounds[3] + _MARGIN_MM
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

    if outline is not None:
        parts.append(
            f'<rect x="{sx(outline[0])}" y="{sy(outline[1])}" '
            f'width="{(outline[2] - outline[0]) * scale:.2f}" '
            f'height="{(outline[3] - outline[1]) * scale:.2f}" '
            f'fill="none" stroke="{_OUTLINE_COLOR}" stroke-width="{0.15 * scale:.2f}"/>'
        )

    pads_by_ref: dict[str, list[Pad]] = {}
    for pad in board.pads:
        pads_by_ref.setdefault(pad.reference, []).append(pad)

    for fp in sorted(board.footprints, key=lambda f: _natural_key(f.reference)):
        pads = sorted(pads_by_ref.get(fp.reference, []), key=lambda p: _natural_key(p.pad_name))
        parts.append(f"<g{_ref_attr(fp.reference)}>")

        # Courtyard rectangle (rotation-aware half-extents), else a rectangle
        # over the footprint's pad centers grown by _PAD_FALLBACK_MM.
        extents = courtyard_extents(fp)
        rect: tuple[float, float, float, float] | None = None  # x1, y1, x2, y2
        if extents is not None:
            rect = (fp.x - extents[0], fp.y - extents[1], fp.x + extents[0], fp.y + extents[1])
        elif pads:
            rect = (
                min(p.x for p in pads) - _PAD_FALLBACK_MM,
                min(p.y for p in pads) - _PAD_FALLBACK_MM,
                max(p.x for p in pads) + _PAD_FALLBACK_MM,
                max(p.y for p in pads) + _PAD_FALLBACK_MM,
            )
        if rect is not None:
            parts.append(
                f'<rect x="{sx(rect[0])}" y="{sy(rect[1])}" '
                f'width="{(rect[2] - rect[0]) * scale:.2f}" '
                f'height="{(rect[3] - rect[1]) * scale:.2f}" fill="{_COURTYARD_FILL}" '
                f'stroke="{_COURTYARD_STROKE}" stroke-width="{0.1 * scale:.2f}"/>'
            )

        for p in pads:
            parts.append(
                f'<circle cx="{sx(p.x)}" cy="{sy(p.y)}" r="{p.radius * scale:.2f}" '
                f'fill="{_PAD_COLOR}"/>'
            )

        # Pin-1 marker: the footprint's first pad (lowest pad_name, natural sort).
        if pads:
            first = pads[0]
            parts.append(
                f'<circle class="pin1" cx="{sx(first.x)}" cy="{sy(first.y)}" '
                f'r="{_PIN1_RADIUS_MM * scale:.2f}" fill="{_PIN1_COLOR}"/>'
            )

        # SVG rotate() is clockwise in screen coordinates while footprint
        # rotation is counterclockwise (matching the pad transform), so the
        # readable angle is negated.
        angle = readable_angle(fp.rotation)
        transform = f' transform="rotate({-angle:g} {sx(fp.x)} {sy(fp.y)})"' if angle else ""
        parts.append(
            f'<text x="{sx(fp.x)}" y="{sy(fp.y)}" font-size="{_TEXT_SIZE_MM * scale:.2f}" '
            f'font-family="sans-serif" fill="{_TEXT_COLOR}" text-anchor="middle" '
            f'dominant-baseline="middle"{transform}>{escape(fp.reference)}</text>'
        )
        parts.append("</g>")

    parts.append("</svg>")
    return "\n".join(parts)


def write_assembly_svg(board: Board, output: Path, scale: float = 20.0) -> None:
    """Write the assembly drawing to ``output``, creating parent directories."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_assembly_svg(board, scale=scale), encoding="utf-8")
