"""Design-rule checks for parsed KiCad boards.

Runs simple electrical clearance and track-width checks over the geometry
in a :class:`~netlist_agent.kicad_pcb.Board`: copper of different nets must
keep ``clearance`` mm of edge-to-edge distance, and every track must be at
least ``min_track_width`` mm wide. Net classes on the board tighten these
rules: a copper pair must clear the larger of its two nets' class
clearances, and a segment whose net class sets a trace width must meet it.
The ``clearance``/``min_track_width`` parameters act as fallback defaults.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from netlist_agent.kicad_pcb import Board, Pad, TrackSegment, Via, clearance_for_net, width_for_net

VIA_COPPER_RADIUS = 0.4  # assumed copper annulus radius of a via, in mm


@dataclass(slots=True)
class DrcViolation:
    kind: str            # "clearance" | "track_width"
    message: str         # human-readable, names the items and actual vs required distance
    x: float             # violation location (midpoint of closest approach, or segment midpoint for width)
    y: float
    net_a: str | None = None
    net_b: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "x": self.x,
            "y": self.y,
            "net_a": self.net_a,
            "net_b": self.net_b,
        }


Point = tuple[float, float]


def _closest_point_on_segment(px: float, py: float, seg: TrackSegment) -> Point:
    """Closest point to (px, py) on the segment's centerline."""
    dx = seg.x2 - seg.x1
    dy = seg.y2 - seg.y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return (seg.x1, seg.y1)
    t = ((px - seg.x1) * dx + (py - seg.y1) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return (seg.x1 + t * dx, seg.y1 + t * dy)


def _segment_intersection(a: TrackSegment, b: TrackSegment) -> Point | None:
    """Intersection point of the two centerlines, or None if they do not cross.

    Parallel/collinear pairs return None; endpoint projections cover them.
    """
    rx, ry = a.x2 - a.x1, a.y2 - a.y1
    sx, sy = b.x2 - b.x1, b.y2 - b.y1
    denom = rx * sy - ry * sx
    if denom == 0.0:
        return None
    qpx, qpy = b.x1 - a.x1, b.y1 - a.y1
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * ry - qpy * rx) / denom
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (a.x1 + t * rx, a.y1 + t * ry)
    return None


def _closest_points_seg_seg(a: TrackSegment, b: TrackSegment) -> tuple[float, Point, Point]:
    """Minimum centerline distance between two segments and the closest point pair."""
    crossing = _segment_intersection(a, b)
    if crossing is not None:
        return (0.0, crossing, crossing)
    best: tuple[float, Point, Point] | None = None
    for px, py, other, flipped in (
        (a.x1, a.y1, b, False),
        (a.x2, a.y2, b, False),
        (b.x1, b.y1, a, True),
        (b.x2, b.y2, a, True),
    ):
        qx, qy = _closest_point_on_segment(px, py, other)
        dist = math.hypot(px - qx, py - qy)
        pair = ((qx, qy), (px, py)) if flipped else ((px, py), (qx, qy))
        if best is None or dist < best[0]:
            best = (dist, pair[0], pair[1])
    assert best is not None
    return best


def _midpoint(p: Point, q: Point) -> Point:
    return ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)


def _describe_segment(seg: TrackSegment) -> str:
    return f"segment ({seg.x1:g}, {seg.y1:g})-({seg.x2:g}, {seg.y2:g}) [{seg.layer}]"


def _describe_pad(pad: Pad) -> str:
    return f"pad {pad.reference}.{pad.pad_name}"


def _describe_via(via: Via) -> str:
    return f"via ({via.x:g}, {via.y:g})"


_Item = tuple[str, int]  # ("s"|"p"|"v", index)


def _candidate_pairs(board: Board, max_clear: float) -> set[tuple[_Item, _Item]]:
    """Conservative spatial prefilter: pairs that could possibly violate.

    Every item is inserted into a uniform grid over its bounding box inflated
    by its copper radius plus half the largest possible clearance; two shapes
    whose edge gap is under any required clearance necessarily share a cell,
    so testing only co-located pairs preserves the exact result set.
    """
    pad = max((p.radius for p in board.pads), default=0.0)
    seg = max((s.width / 2 for s in board.segments), default=0.0)
    cell = max(1.0, 2 * max(pad, seg, VIA_COPPER_RADIUS) + max_clear)

    grid: dict[tuple[int, int], list[_Item]] = {}

    def insert(item: _Item, x1: float, y1: float, x2: float, y2: float, reach: float) -> None:
        pad_out = reach + max_clear / 2
        c_lo = int((min(x1, x2) - pad_out) // cell)
        c_hi = int((max(x1, x2) + pad_out) // cell)
        r_lo = int((min(y1, y2) - pad_out) // cell)
        r_hi = int((max(y1, y2) + pad_out) // cell)
        for c in range(c_lo, c_hi + 1):
            for r in range(r_lo, r_hi + 1):
                grid.setdefault((c, r), []).append(item)

    for i, s in enumerate(board.segments):
        insert(("s", i), s.x1, s.y1, s.x2, s.y2, s.width / 2)
    for i, p in enumerate(board.pads):
        insert(("p", i), p.x, p.y, p.x, p.y, p.radius)
    for i, v in enumerate(board.vias):
        insert(("v", i), v.x, v.y, v.x, v.y, VIA_COPPER_RADIUS)

    pairs: set[tuple[_Item, _Item]] = set()
    for items in grid.values():
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                pair = (items[a], items[b]) if items[a] <= items[b] else (items[b], items[a])
                pairs.add(pair)
    return pairs


def check_board(board: Board, clearance: float = 0.15, min_track_width: float = 0.15) -> list[DrcViolation]:
    """Run clearance and track-width checks; return violations sorted by (kind, x, y)."""
    violations: list[DrcViolation] = []

    def net_name(code: int) -> str:
        return board.nets.get(code) or f"#{code}"

    def required_clearance(code_a: int, code_b: int) -> float:
        return max(
            clearance_for_net(board, code_a, default=clearance),
            clearance_for_net(board, code_b, default=clearance),
        )

    def check_clearance(dist: float, at: Point, item_a: str, item_b: str, code_a: int, code_b: int) -> None:
        required = required_clearance(code_a, code_b)
        if dist >= required:
            return
        violations.append(
            DrcViolation(
                kind="clearance",
                message=(
                    f"clearance {round(dist, 3)} mm < required {round(required, 3)} mm "
                    f"between {item_a} (net {net_name(code_a)}) and {item_b} (net {net_name(code_b)})"
                ),
                x=at[0],
                y=at[1],
                net_a=net_name(code_a),
                net_b=net_name(code_b),
            )
        )

    segments = board.segments
    pads = board.pads
    vias = board.vias

    max_clear = max(
        [clearance]
        + [nc.clearance for nc in board.net_classes.values() if nc.clearance is not None]
    )

    # Clearance checks over spatially co-located pairs only; the prefilter is
    # conservative, so the violation set matches the full pairwise sweep.
    for (kind_a, i), (kind_b, j) in sorted(_candidate_pairs(board, max_clear)):
        if kind_a == "p" and kind_b == "p":
            pad_a, pad_b = pads[i], pads[j]
            if pad_a.net_code == pad_b.net_code:
                continue
            center_dist = math.hypot(pad_a.x - pad_b.x, pad_a.y - pad_b.y)
            edge_dist = center_dist - pad_a.radius - pad_b.radius
            check_clearance(
                edge_dist, _midpoint((pad_a.x, pad_a.y), (pad_b.x, pad_b.y)),
                _describe_pad(pad_a), _describe_pad(pad_b),
                pad_a.net_code, pad_b.net_code,
            )
        elif kind_a == "p" and kind_b == "s":
            pad, seg = pads[i], segments[j]
            if pad.net_code == seg.net_code:
                continue
            qx, qy = _closest_point_on_segment(pad.x, pad.y, seg)
            edge_dist = math.hypot(pad.x - qx, pad.y - qy) - pad.radius - seg.width / 2
            check_clearance(
                edge_dist, _midpoint((pad.x, pad.y), (qx, qy)),
                _describe_pad(pad), _describe_segment(seg),
                pad.net_code, seg.net_code,
            )
        elif kind_a == "s" and kind_b == "s":
            seg_a, seg_b = segments[i], segments[j]
            if seg_a.net_code == seg_b.net_code or seg_a.layer != seg_b.layer:
                continue
            center_dist, pa, pb = _closest_points_seg_seg(seg_a, seg_b)
            edge_dist = center_dist - seg_a.width / 2 - seg_b.width / 2
            check_clearance(
                edge_dist, _midpoint(pa, pb),
                _describe_segment(seg_a), _describe_segment(seg_b),
                seg_a.net_code, seg_b.net_code,
            )
        elif kind_a == "s" and kind_b == "v":
            seg, via = segments[i], vias[j]
            if via.net_code == seg.net_code:
                continue
            qx, qy = _closest_point_on_segment(via.x, via.y, seg)
            edge_dist = math.hypot(via.x - qx, via.y - qy) - VIA_COPPER_RADIUS - seg.width / 2
            check_clearance(
                edge_dist, _midpoint((via.x, via.y), (qx, qy)),
                _describe_via(via), _describe_segment(seg),
                via.net_code, seg.net_code,
            )
        elif kind_a == "p" and kind_b == "v":
            pad, via = pads[i], vias[j]
            if via.net_code == pad.net_code:
                continue
            center_dist = math.hypot(via.x - pad.x, via.y - pad.y)
            edge_dist = center_dist - VIA_COPPER_RADIUS - pad.radius
            check_clearance(
                edge_dist, _midpoint((via.x, via.y), (pad.x, pad.y)),
                _describe_via(via), _describe_pad(pad),
                via.net_code, pad.net_code,
            )
        elif kind_a == "v" and kind_b == "v":
            via, via_b = vias[i], vias[j]
            if via.net_code == via_b.net_code:
                continue
            center_dist = math.hypot(via.x - via_b.x, via.y - via_b.y)
            edge_dist = center_dist - 2 * VIA_COPPER_RADIUS
            check_clearance(
                edge_dist, _midpoint((via.x, via.y), (via_b.x, via_b.y)),
                _describe_via(via), _describe_via(via_b),
                via.net_code, via_b.net_code,
            )

    # 5. track width (width 0 means "unspecified" in some files: skip)
    for seg in segments:
        required_width = max(min_track_width, width_for_net(board, seg.net_code, default=min_track_width))
        if 0 < seg.width < required_width:
            violations.append(
                DrcViolation(
                    kind="track_width",
                    message=(
                        f"track width {round(seg.width, 3)} mm < minimum {round(required_width, 3)} mm "
                        f"for {_describe_segment(seg)} (net {net_name(seg.net_code)})"
                    ),
                    x=(seg.x1 + seg.x2) / 2,
                    y=(seg.y1 + seg.y2) / 2,
                    net_a=net_name(seg.net_code),
                )
            )

    violations.sort(key=lambda v: (v.kind, v.x, v.y))
    return violations
