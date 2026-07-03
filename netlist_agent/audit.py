"""Board-quality audit: flag suspicious-but-legal copper on a parsed board.

Complements DRC (:mod:`netlist_agent.drc`) with heuristics for issues that
pass rule checks but usually signal a mistake: nets whose only copper is a
single pad, track stubs that end in mid-air, duplicated or zero-length
segments, and pads without a net or a reference designator.

Connectivity for the antenna check mirrors :mod:`netlist_agent.ratsnest`:
a segment endpoint counts as connected if it lands within a same-net pad's
hit radius, coincides (on ratsnest's 1 µm grid) with another same-net
segment endpoint or via, or lies inside a same-net zone polygon.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .kicad_pcb import Board, Pad, TrackSegment
from .ratsnest import _COORD_KEY_DECIMALS, _point_in_polygon

_PAD_HIT_EPS = 1e-6  # same slack as ratsnest's pad hit test
_ZERO_LENGTH_EPS = 1e-6
_SUMMARY_REF_LIMIT = 8  # pad references listed in a summary finding


@dataclass(slots=True)
class AuditFinding:
    kind: str  # "single_pad_net" | "unconnected_pad" | "antenna_track"
    #            | "duplicate_segment" | "zero_length_segment" | "missing_reference"
    message: str
    reference: str | None = None
    net: str | None = None
    x: float | None = None
    y: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "reference": self.reference,
            "net": self.net,
            "x": self.x,
            "y": self.y,
        }


def audit_board(board: Board) -> list[AuditFinding]:
    """Run all audit checks; findings sorted by (kind, net, reference)."""

    def net_name(code: int) -> str:
        return board.nets.get(code) or f"#{code}"

    findings: list[AuditFinding] = []
    findings.extend(_single_pad_nets(board, net_name))
    findings.extend(_unconnected_pads(board))
    findings.extend(_antenna_tracks(board, net_name))
    findings.extend(_duplicate_segments(board, net_name))
    findings.extend(_zero_length_segments(board, net_name))
    findings.extend(_missing_references(board))
    findings.sort(
        key=lambda f: (f.kind, f.net or "", f.reference or "", f.x or 0.0, f.y or 0.0, f.message)
    )
    return findings


def _grid_key(x: float, y: float) -> tuple[float, float]:
    return (round(x, _COORD_KEY_DECIMALS), round(y, _COORD_KEY_DECIMALS))


def _pad_summary(pads: list[Pad]) -> str:
    refs = sorted(f"{p.reference}.{p.pad_name}" for p in pads)
    listed = ", ".join(refs[:_SUMMARY_REF_LIMIT])
    return listed + (", ..." if len(refs) > _SUMMARY_REF_LIMIT else "")


def _single_pad_nets(board: Board, net_name) -> list[AuditFinding]:
    """Nets (code != 0) whose copper is exactly one pad and nothing else."""
    pads_by_net: dict[int, list[Pad]] = {}
    for pad in board.pads:
        if pad.net_code != 0:
            pads_by_net.setdefault(pad.net_code, []).append(pad)
    nets_with_other_copper = (
        {s.net_code for s in board.segments}
        | {v.net_code for v in board.vias}
        | {z.net_code for z in board.zones}
    )
    findings: list[AuditFinding] = []
    for code, pads in sorted(pads_by_net.items()):
        if len(pads) != 1 or code in nets_with_other_copper:
            continue
        (pad,) = pads
        findings.append(
            AuditFinding(
                kind="single_pad_net",
                message=(
                    f"net {net_name(code)!r} consists of the single pad "
                    f"{pad.reference}.{pad.pad_name} and no other copper"
                ),
                reference=pad.reference,
                net=net_name(code),
                x=pad.x,
                y=pad.y,
            )
        )
    return findings


def _unconnected_pads(board: Board) -> list[AuditFinding]:
    """One summary finding for pads with no net assigned (net 0)."""
    loose = [p for p in board.pads if p.net_code == 0]
    if not loose:
        return []
    return [
        AuditFinding(
            kind="unconnected_pad",
            message=f"{len(loose)} pad(s) have no net assigned: {_pad_summary(loose)}",
        )
    ]


def _antenna_tracks(board: Board, net_name) -> list[AuditFinding]:
    """Segment endpoints that touch no same-net pad, endpoint, via, or zone."""
    findings: list[AuditFinding] = []
    for code in sorted({s.net_code for s in board.segments}):
        segments = [s for s in board.segments if s.net_code == code]
        pads = [p for p in board.pads if p.net_code == code]
        via_keys = {_grid_key(v.x, v.y) for v in board.vias if v.net_code == code}
        polygons = [poly for z in board.zones if z.net_code == code for poly in z.polygons]

        endpoint_counts: Counter[tuple[float, float]] = Counter()
        for seg in segments:
            endpoint_counts[_grid_key(seg.x1, seg.y1)] += 1
            endpoint_counts[_grid_key(seg.x2, seg.y2)] += 1

        for seg in segments:
            for x, y in ((seg.x1, seg.y1), (seg.x2, seg.y2)):
                key = _grid_key(x, y)
                if endpoint_counts[key] >= 2 or key in via_keys:
                    continue
                if any(math.hypot(p.x - x, p.y - y) <= p.radius + _PAD_HIT_EPS for p in pads):
                    continue
                if any(_point_in_polygon(x, y, poly) for poly in polygons):
                    continue
                findings.append(
                    AuditFinding(
                        kind="antenna_track",
                        message=(
                            f"track on net {net_name(code)!r} dangles at "
                            f"({x:g}, {y:g}) [{seg.layer}]"
                        ),
                        net=net_name(code),
                        x=x,
                        y=y,
                    )
                )
    return findings


def _duplicate_segments(board: Board, net_name) -> list[AuditFinding]:
    """Same-net, same-layer segments with identical unordered endpoints."""
    groups: dict[tuple[Any, ...], list[TrackSegment]] = {}
    for seg in board.segments:
        a, b = _grid_key(seg.x1, seg.y1), _grid_key(seg.x2, seg.y2)
        ends = (a, b) if a <= b else (b, a)
        groups.setdefault((seg.net_code, seg.layer, ends), []).append(seg)
    findings: list[AuditFinding] = []
    for (code, layer, ends), segs in sorted(groups.items()):
        (ax, ay), (bx, by) = ends
        for _ in range(len(segs) * (len(segs) - 1) // 2):  # one finding per pair
            findings.append(
                AuditFinding(
                    kind="duplicate_segment",
                    message=(
                        f"duplicate segment on net {net_name(code)!r} between "
                        f"({ax:g}, {ay:g}) and ({bx:g}, {by:g}) [{layer}]"
                    ),
                    net=net_name(code),
                    x=(ax + bx) / 2,
                    y=(ay + by) / 2,
                )
            )
    return findings


def _zero_length_segments(board: Board, net_name) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for seg in board.segments:
        if seg.length < _ZERO_LENGTH_EPS:
            findings.append(
                AuditFinding(
                    kind="zero_length_segment",
                    message=(
                        f"zero-length segment on net {net_name(seg.net_code)!r} at "
                        f"({seg.x1:g}, {seg.y1:g}) [{seg.layer}]"
                    ),
                    net=net_name(seg.net_code),
                    x=seg.x1,
                    y=seg.y1,
                )
            )
    return findings


def _missing_references(board: Board) -> list[AuditFinding]:
    """One summary finding for pads whose footprint lacks a reference."""
    nameless = [p for p in board.pads if p.reference in ("?", "")]
    if not nameless:
        return []
    return [
        AuditFinding(
            kind="missing_reference",
            message=f"{len(nameless)} pad(s) belong to a footprint without a reference designator",
        )
    ]
