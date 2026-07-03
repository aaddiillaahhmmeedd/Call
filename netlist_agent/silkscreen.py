"""Silkscreen reference-designator layer export as RS-274X Gerber.

Renders each footprint's reference text with a minimal vector stroke font,
placed just above the footprint's courtyard. The board model is y-down while
Gerber's Y axis points up, so all emitted y coordinates are negated
(millimeters, 4.6 coordinate format, matching :mod:`netlist_agent.gerber`).
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from .kicad_pcb import Board, Footprint

TEXT_APERTURE = 0.15  # silkscreen stroke width in mm
GLYPH_WIDTH = 0.6  # glyph box width as a fraction of text height
ADVANCE = 0.8  # per-character advance as a fraction of text height
DEFAULT_CLEARANCE = 1.5  # fallback half-height when a footprint lacks a courtyard

# Stroke segments (x1, y1, x2, y2) per glyph, in a unit box with x in
# [0, GLYPH_WIDTH] and y in [0, 1], y-up within the glyph box.
STROKES: dict[str, list[tuple[float, float, float, float]]] = {
    "0": [(0, 0, 0, 1), (0.6, 0, 0.6, 1), (0, 1, 0.6, 1), (0, 0, 0.6, 0)],
    "1": [(0.3, 0, 0.3, 1), (0.1, 0.8, 0.3, 1), (0, 0, 0.6, 0)],
    "2": [(0, 1, 0.6, 1), (0.6, 0.5, 0.6, 1), (0, 0.5, 0.6, 0.5), (0, 0, 0, 0.5), (0, 0, 0.6, 0)],
    "3": [(0, 1, 0.6, 1), (0.6, 0, 0.6, 1), (0, 0.5, 0.6, 0.5), (0, 0, 0.6, 0)],
    "4": [(0, 0.5, 0, 1), (0, 0.5, 0.6, 0.5), (0.6, 0, 0.6, 1)],
    "5": [(0, 1, 0.6, 1), (0, 0.5, 0, 1), (0, 0.5, 0.6, 0.5), (0.6, 0, 0.6, 0.5), (0, 0, 0.6, 0)],
    "6": [(0, 1, 0.6, 1), (0, 0, 0, 1), (0, 0.5, 0.6, 0.5), (0.6, 0, 0.6, 0.5), (0, 0, 0.6, 0)],
    "7": [(0, 1, 0.6, 1), (0.6, 0, 0.6, 1)],
    "8": [(0, 0, 0, 1), (0.6, 0, 0.6, 1), (0, 1, 0.6, 1), (0, 0.5, 0.6, 0.5), (0, 0, 0.6, 0)],
    "9": [(0, 0.5, 0, 1), (0, 1, 0.6, 1), (0, 0.5, 0.6, 0.5), (0.6, 0, 0.6, 1), (0, 0, 0.6, 0)],
    "A": [(0, 0, 0, 1), (0.6, 0, 0.6, 1), (0, 1, 0.6, 1), (0, 0.5, 0.6, 0.5)],
    "B": [(0, 0, 0, 1), (0, 1, 0.5, 1), (0.5, 0.5, 0.5, 1), (0, 0.5, 0.6, 0.5), (0.6, 0, 0.6, 0.5), (0, 0, 0.6, 0)],
    "C": [(0, 1, 0.6, 1), (0, 0, 0, 1), (0, 0, 0.6, 0)],
    "D": [(0, 0, 0, 1), (0, 1, 0.45, 1), (0.45, 1, 0.6, 0.7), (0.6, 0.3, 0.6, 0.7), (0.45, 0, 0.6, 0.3), (0, 0, 0.45, 0)],
    "E": [(0, 0, 0, 1), (0, 1, 0.6, 1), (0, 0.5, 0.5, 0.5), (0, 0, 0.6, 0)],
    "F": [(0, 0, 0, 1), (0, 1, 0.6, 1), (0, 0.5, 0.5, 0.5)],
    "G": [(0, 1, 0.6, 1), (0, 0, 0, 1), (0, 0, 0.6, 0), (0.6, 0, 0.6, 0.5), (0.3, 0.5, 0.6, 0.5)],
    "H": [(0, 0, 0, 1), (0.6, 0, 0.6, 1), (0, 0.5, 0.6, 0.5)],
    "I": [(0.3, 0, 0.3, 1), (0.1, 1, 0.5, 1), (0.1, 0, 0.5, 0)],
    "J": [(0.6, 0, 0.6, 1), (0, 0, 0.6, 0), (0, 0, 0, 0.3)],
    "K": [(0, 0, 0, 1), (0, 0.5, 0.6, 1), (0, 0.5, 0.6, 0)],
    "L": [(0, 0, 0, 1), (0, 0, 0.6, 0)],
    "M": [(0, 0, 0, 1), (0.6, 0, 0.6, 1), (0, 1, 0.3, 0.5), (0.3, 0.5, 0.6, 1)],
    "N": [(0, 0, 0, 1), (0.6, 0, 0.6, 1), (0, 1, 0.6, 0)],
    "O": [(0, 0, 0, 1), (0.6, 0, 0.6, 1), (0, 1, 0.6, 1), (0, 0, 0.6, 0)],
    "P": [(0, 0, 0, 1), (0, 1, 0.6, 1), (0.6, 0.5, 0.6, 1), (0, 0.5, 0.6, 0.5)],
    "Q": [(0, 0, 0, 1), (0.6, 0, 0.6, 1), (0, 1, 0.6, 1), (0, 0, 0.6, 0), (0.35, 0.3, 0.6, 0)],
    "R": [(0, 0, 0, 1), (0, 1, 0.6, 1), (0.6, 0.5, 0.6, 1), (0, 0.5, 0.6, 0.5), (0.3, 0.5, 0.6, 0)],
    "S": [(0, 1, 0.6, 1), (0, 0.5, 0, 1), (0, 0.5, 0.6, 0.5), (0.6, 0, 0.6, 0.5), (0, 0, 0.6, 0)],
    "T": [(0, 1, 0.6, 1), (0.3, 0, 0.3, 1)],
    "U": [(0, 0, 0, 1), (0.6, 0, 0.6, 1), (0, 0, 0.6, 0)],
    "V": [(0, 1, 0.3, 0), (0.3, 0, 0.6, 1)],
    "W": [(0, 1, 0.15, 0), (0.15, 0, 0.3, 0.5), (0.3, 0.5, 0.45, 0), (0.45, 0, 0.6, 1)],
    "X": [(0, 0, 0.6, 1), (0, 1, 0.6, 0)],
    "Y": [(0, 1, 0.3, 0.5), (0.6, 1, 0.3, 0.5), (0.3, 0, 0.3, 0.5)],
    "Z": [(0, 1, 0.6, 1), (0, 0, 0.6, 1), (0, 0, 0.6, 0)],
    "_": [(0, 0, 0.6, 0)],
    "-": [(0.1, 0.5, 0.5, 0.5)],
    "?": [(0, 0.8, 0, 1), (0, 1, 0.6, 1), (0.6, 0.5, 0.6, 1), (0.3, 0.3, 0.6, 0.5), (0.3, 0, 0.3, 0.1)],
    "+": [(0.3, 0.2, 0.3, 0.8), (0, 0.5, 0.6, 0.5)],
    ".": [(0.25, 0, 0.35, 0)],
}


def _coord(value_mm: float) -> str:
    """Millimeters -> 4.6 fixed-format integer string (mm * 1e6)."""
    return str(int(round(value_mm * 1_000_000)))


def _xy(x: float, y: float) -> str:
    """Encode a board-model point, negating y for Gerber's y-up axis."""
    return f"X{_coord(x)}Y{_coord(-y)}"


def text_strokes(
    text: str, x: float, y: float, height: float = 1.0, rotation: float = 0.0
) -> list[tuple[float, float, float, float]]:
    """Absolute mm stroke segments for *text* centered on (x, y).

    Characters are laid out left-to-right with an advance of ``ADVANCE *
    height``, scaled to *height*, and rotated by *rotation* degrees about
    (x, y) using the board's y-down convention (like ``kicad_pcb._parse_pad``).
    Unknown characters advance without contributing strokes.
    """
    advance = ADVANCE * height
    ink_width = advance * (len(text) - 1) + GLYPH_WIDTH * height if text else 0.0
    theta = math.radians(rotation)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    segments: list[tuple[float, float, float, float]] = []
    for index, char in enumerate(text.upper()):
        for gx1, gy1, gx2, gy2 in STROKES.get(char, ()):
            points: list[float] = []
            for gx, gy in ((gx1, gy1), (gx2, gy2)):
                # Glyph box is y-up; the board is y-down, so flip about the
                # vertical center of the string before rotating.
                dx = index * advance + gx * height - ink_width / 2
                dy = (0.5 - gy) * height
                points.append(x + dx * cos_t + dy * sin_t)
                points.append(y - dx * sin_t + dy * cos_t)
            segments.append((points[0], points[1], points[2], points[3]))
    return segments


def _natural_key(reference: str) -> list[str | int]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", reference)]


def _readable_rotation(rotation: float) -> float:
    """Footprint rotation normalized so the text stays readable.

    Angles whose value mod 360 falls in (90, 270] would render the text
    upside-down; those are flipped by 180 degrees.
    """
    angle = rotation % 360
    if 90 < angle <= 270:
        angle = (angle + 180) % 360
    return angle


def _reference_strokes(
    footprint: Footprint, text_height: float
) -> list[tuple[float, float, float, float]]:
    half_h = footprint.courtyard_half_h if footprint.courtyard_half_h is not None else DEFAULT_CLEARANCE
    # Board y is down, so "above" the footprint means a smaller y.
    text_y = footprint.y - half_h - text_height
    return text_strokes(
        footprint.reference,
        footprint.x,
        text_y,
        height=text_height,
        rotation=_readable_rotation(footprint.rotation),
    )


def export_silkscreen(board: Board, layer: str = "F.SilkS", text_height: float = 1.0) -> str:
    """Render the reference designators of *board* as an RS-274X Gerber file."""
    lines: list[str] = [
        "%FSLAX46Y46*%",
        "%MOMM*%",
        "%LPD*%",
        f"G04 Layer: {layer}*",
        f"%ADD10C,{TEXT_APERTURE:.3f}*%",
        "D10*",
    ]

    footprints = [fp for fp in board.footprints if fp.reference not in ("", "?")]
    for footprint in sorted(footprints, key=lambda fp: _natural_key(fp.reference)):
        lines.append(f"G04 {footprint.reference}*")
        for x1, y1, x2, y2 in _reference_strokes(footprint, text_height):
            lines.append(f"{_xy(x1, y1)}D02*")
            lines.append(f"{_xy(x2, y2)}D01*")

    lines.append("M02*")
    return "\n".join(lines) + "\n"


def write_silkscreen(board: Board, output: Path, text_height: float = 1.0) -> None:
    """Write the silkscreen Gerber for *board* to *output*, creating parent dirs."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(export_silkscreen(board, text_height=text_height), encoding="utf-8")
