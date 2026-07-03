"""Gerber RS-274X / Excellon export for :class:`~netlist_agent.kicad_pcb.Board`.

Exports copper layers as RS-274X (millimeters, 4.6 coordinate format) and via
holes as an Excellon drill file. The board model is y-down while Gerber's Y
axis points up, so all emitted y coordinates are negated.
"""

from __future__ import annotations

import math
from pathlib import Path

from .kicad_pcb import ArcTrack, Board, Pad, TrackSegment, Via, Zone

VIA_DIAMETER = 0.8  # copper annulus flashed for each via
VIA_DRILL = 0.4  # default via hole diameter
OUTLINE_WIDTH = 0.10  # stroke width of the Edge.Cuts outline aperture


def _coord(value_mm: float) -> str:
    """Millimeters -> 4.6 fixed-format integer string (mm * 1e6)."""
    return str(int(round(value_mm * 1_000_000)))


def _xy(x: float, y: float) -> str:
    """Encode a board-model point, negating y for Gerber's y-up axis."""
    return f"X{_coord(x)}Y{_coord(-y)}"


def export_layer(board: Board, layer: str) -> str:
    """Render one copper layer of *board* as an RS-274X Gerber file."""
    segments: list[TrackSegment] = [s for s in board.segments if s.layer == layer]
    arcs: list[ArcTrack] = [a for a in board.arcs if a.layer == layer]
    zones: list[Zone] = [z for z in board.zones if z.layer == layer]
    pads: list[Pad] = board.pads
    vias: list[Via] = board.vias

    lines: list[str] = [
        "%FSLAX46Y46*%",
        "%MOMM*%",
        "%LPD*%",
        f"G04 Layer: {layer}*",
    ]

    # Aperture definitions: track widths, then pad diameters, then the via
    # aperture; each group sorted ascending for deterministic numbering.
    code = 10
    width_apertures: dict[float, int] = {}
    for width in sorted({s.width for s in segments} | {a.width for a in arcs}):
        width_apertures[width] = code
        lines.append(f"%ADD{code}C,{width:.3f}*%")
        code += 1
    pad_apertures: dict[float, int] = {}
    for diameter in sorted({2 * p.radius for p in pads}):
        pad_apertures[diameter] = code
        lines.append(f"%ADD{code}C,{diameter:.3f}*%")
        code += 1
    via_aperture: int | None = None
    if vias:
        via_aperture = code
        lines.append(f"%ADD{code}C,{VIA_DIAMETER:.3f}*%")

    for segment in segments:
        lines.append(f"D{width_apertures[segment.width]}*")
        lines.append(f"{_xy(segment.x1, segment.y1)}D02*")
        lines.append(f"{_xy(segment.x2, segment.y2)}D01*")

    if arcs:
        lines.append("G75*")  # multi-quadrant circular interpolation
        for arc in arcs:
            lines.append(f"D{width_apertures[arc.width]}*")
            lines.append(f"{_xy(arc.x1, arc.y1)}D02*")
            center = arc._center()
            if center is None:  # collinear points: draw the chord instead
                lines.append(f"{_xy(arc.x2, arc.y2)}D01*")
                continue
            cx, cy = center
            theta_start = math.atan2(arc.y1 - cy, arc.x1 - cx)
            ccw_end = (math.atan2(arc.y2 - cy, arc.x2 - cx) - theta_start) % math.tau
            ccw_mid = (math.atan2(arc.ym - cy, arc.xm - cx) - theta_start) % math.tau
            # A counter-clockwise sweep in board coords (ccw_mid <= ccw_end, as
            # in svg_render) turns clockwise once y is negated: emit G02.
            interpolation = "G02" if ccw_mid <= ccw_end else "G03"
            offsets = f"I{_coord(cx - arc.x1)}J{_coord(arc.y1 - cy)}"  # center - start, y negated
            lines.append(f"{interpolation}{_xy(arc.x2, arc.y2)}{offsets}D01*")
        lines.append("G01*")  # back to linear interpolation

    for pad in pads:
        lines.append(f"D{pad_apertures[2 * pad.radius]}*")
        lines.append(f"{_xy(pad.x, pad.y)}D03*")

    for via in vias:
        lines.append(f"D{via_aperture}*")
        lines.append(f"{_xy(via.x, via.y)}D03*")

    for zone in zones:
        for polygon in zone.polygons:
            if not polygon:
                continue
            lines.append("G36*")
            first = polygon[0]
            lines.append(f"{_xy(first[0], first[1])}D02*")
            for x, y in polygon[1:]:
                lines.append(f"{_xy(x, y)}D01*")
            lines.append(f"{_xy(first[0], first[1])}D01*")
            lines.append("G37*")

    lines.append("M02*")
    return "\n".join(lines) + "\n"


def export_outline(board: Board) -> str:
    """Render the board outline (Edge.Cuts) of *board* as an RS-274X Gerber file."""
    lines: list[str] = [
        "%FSLAX46Y46*%",
        "%MOMM*%",
        "%LPD*%",
        "G04 Layer: Edge.Cuts*",
    ]
    if board.edge_segments:
        lines.append(f"%ADD10C,{OUTLINE_WIDTH:.3f}*%")
        lines.append("D10*")
        for segment in board.edge_segments:
            lines.append(f"{_xy(segment.x1, segment.y1)}D02*")
            lines.append(f"{_xy(segment.x2, segment.y2)}D01*")
    lines.append("M02*")
    return "\n".join(lines) + "\n"


def export_drill(board: Board) -> str:
    """Render the via holes of *board* as an Excellon drill file."""
    lines: list[str] = ["M48", "METRIC,TZ"]
    if board.vias:
        lines.append(f"T1C{VIA_DRILL:.3f}")
    lines.append("%")
    if board.vias:
        lines.append("T1")
        for via in board.vias:
            lines.append(f"X{via.x:.3f}Y{-via.y:.3f}")
    lines.append("M30")
    return "\n".join(lines) + "\n"


def export_gerbers(
    board: Board, output_dir: Path, layers: tuple[str, ...] = ("F.Cu", "B.Cu")
) -> list[Path]:
    """Write Gerber files for *layers* plus the outline and drill files; return sorted paths."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "board"
    written: list[Path] = []
    for layer in layers:
        path = output_dir / f"{stem}-{layer.replace('.', '_')}.gbr"
        path.write_text(export_layer(board, layer), encoding="utf-8")
        written.append(path)
    outline_path = output_dir / f"{stem}-Edge_Cuts.gbr"
    outline_path.write_text(export_outline(board), encoding="utf-8")
    written.append(outline_path)
    drill_path = output_dir / f"{stem}-drill.drl"
    drill_path.write_text(export_drill(board), encoding="utf-8")
    written.append(drill_path)
    return sorted(written)
