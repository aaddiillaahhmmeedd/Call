"""Teardrop generation: reinforcement copper where a track enters a pad.

For every same-net (pad, segment-endpoint) contact — endpoint within the
pad's hit radius, the ratsnest convention — a triangular wedge is emitted:
base across the pad (2 * width_ratio * radius wide), apex a distance of
length_ratio * radius down the track (clamped to 80% of the segment). Fat
tracks (width >= pad diameter) need no reinforcement and are skipped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from .kicad_pcb import Board, Zone

_PAD_HIT_EPS = 1e-6
_MAX_SEGMENT_FRACTION = 0.8


@dataclass(slots=True)
class Teardrop:
    reference: str
    pad_name: str
    net_code: int
    layer: str
    polygon: list[tuple[float, float]]


def generate_teardrops(
    board: Board, length_ratio: float = 1.0, width_ratio: float = 0.9
) -> list[Teardrop]:
    teardrops: list[Teardrop] = []
    for pad in board.pads:
        if pad.net_code == 0:
            continue
        for seg in board.segments:
            if seg.net_code != pad.net_code or seg.width >= 2 * pad.radius:
                continue
            for ex, ey, ox, oy in (
                (seg.x1, seg.y1, seg.x2, seg.y2),
                (seg.x2, seg.y2, seg.x1, seg.y1),
            ):
                if math.hypot(ex - pad.x, ey - pad.y) > pad.radius + _PAD_HIT_EPS:
                    continue
                if math.hypot(ox - pad.x, oy - pad.y) <= pad.radius + _PAD_HIT_EPS:
                    continue  # whole segment inside the pad: nothing to reinforce
                span = math.hypot(ox - ex, oy - ey)
                if span <= 0:
                    continue
                dx, dy = (ox - ex) / span, (oy - ey) / span
                length = min(length_ratio * pad.radius, _MAX_SEGMENT_FRACTION * span)
                half_base = width_ratio * pad.radius
                apex = (round(ex + dx * length, 4), round(ey + dy * length, 4))
                base_a = (round(ex - dy * half_base, 4), round(ey + dx * half_base, 4))
                base_b = (round(ex + dy * half_base, 4), round(ey - dx * half_base, 4))
                teardrops.append(
                    Teardrop(
                        reference=pad.reference,
                        pad_name=pad.pad_name,
                        net_code=pad.net_code,
                        layer=seg.layer,
                        polygon=[base_a, base_b, apex],
                    )
                )
    teardrops.sort(key=lambda t: (t.reference, t.pad_name, t.layer, t.polygon[2]))
    return teardrops


def teardrops_to_zones(teardrops: list[Teardrop]) -> list[Zone]:
    """One zone per (net, layer) collecting that group's wedge polygons."""
    groups: dict[tuple[int, str], list[list[tuple[float, float]]]] = {}
    for teardrop in teardrops:
        groups.setdefault((teardrop.net_code, teardrop.layer), []).append(teardrop.polygon)
    return [
        Zone(net_code=net_code, layer=layer, polygons=polygons)
        for (net_code, layer), polygons in sorted(groups.items())
    ]


def _fmt(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def write_teardropped_board(source: Path, teardrops: list[Teardrop], output: Path) -> None:
    """Insert the teardrop zones into the source board text, before the final ``)``."""
    text = Path(source).read_text(encoding="utf-8", errors="ignore")
    close = text.rindex(")")
    blocks: list[str] = []
    for zone in teardrops_to_zones(teardrops):
        polygons = "".join(
            f'    (filled_polygon (layer "{zone.layer}") (pts '
            + " ".join(f"(xy {_fmt(x)} {_fmt(y)})" for x, y in polygon)
            + "))\n"
            for polygon in zone.polygons
        )
        blocks.append(f'  (zone (net {zone.net_code}) (layer "{zone.layer}")\n{polygons}  )\n')
    lines = "".join(blocks)
    if close > 0 and text[close - 1] != "\n":
        lines = "\n" + lines
    Path(output).write_text(text[:close] + lines + text[close:], encoding="utf-8")
