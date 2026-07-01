"""Ratsnest computation: find unrouted connections (airwires) on a board.

Algorithm, per net:

1. Collect connection items: pads, track-segment endpoints, vias, and zones.
2. Cluster them with union-find — a segment joins its two endpoints, a
   pad absorbs any endpoint/via that lands within its hit radius, and a
   zone absorbs any item point inside one of its filled polygons.
3. Every pair of clusters still separated needs copper: emit the minimum
   spanning tree over clusters (Prim's), using the closest item pair between
   two clusters as the edge — the same shape KiCad draws as its ratsnest.

Connectivity is layer-agnostic (a front-layer track endpoint touching a
back-layer track endpoint counts as joined). That slightly under-reports
airwires on boards with stacked, unconnected tracks, which is rare and
acceptable for a v1 reporting bot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .kicad_pcb import Board

_COORD_KEY_DECIMALS = 3  # 1 µm grid for point identity


@dataclass(slots=True)
class Airwire:
    net_code: int
    net_name: str
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "net": self.net_name,
            "from": [self.x1, self.y1],
            "to": [self.x2, self.y2],
            "length_mm": round(self.length, 3),
        }


@dataclass(slots=True)
class NetReport:
    net_code: int
    net_name: str
    pad_count: int
    cluster_count: int
    routed_length: float
    airwires: list[Airwire] = field(default_factory=list)

    @property
    def unrouted_length(self) -> float:
        return sum(a.length for a in self.airwires)

    @property
    def fully_routed(self) -> bool:
        return not self.airwires

    def to_dict(self) -> dict[str, Any]:
        return {
            "net": self.net_name,
            "pads": self.pad_count,
            "clusters": self.cluster_count,
            "fully_routed": self.fully_routed,
            "routed_length_mm": round(self.routed_length, 3),
            "unrouted_length_mm": round(self.unrouted_length, 3),
            "airwires": [a.to_dict() for a in self.airwires],
        }


@dataclass(slots=True)
class RatsnestReport:
    nets: list[NetReport]

    @property
    def airwires(self) -> list[Airwire]:
        return [a for net in self.nets for a in net.airwires]

    def to_dict(self) -> dict[str, Any]:
        routed = sum(1 for n in self.nets if n.fully_routed)
        return {
            "nets_total": len(self.nets),
            "nets_fully_routed": routed,
            "completion_pct": round(100 * routed / len(self.nets), 1) if self.nets else 100.0,
            "airwire_count": len(self.airwires),
            "unrouted_length_mm": round(sum(n.unrouted_length for n in self.nets), 3),
            "nets": [n.to_dict() for n in self.nets],
        }


_BOUNDARY_EPS = 1e-6


def _on_segment(x: float, y: float, x1: float, y1: float, x2: float, y2: float) -> bool:
    dx, dy = x2 - x1, y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(x - x1, y - y1) <= _BOUNDARY_EPS
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length_sq))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)) <= _BOUNDARY_EPS


def _point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """Even-odd ray casting; points within _BOUNDARY_EPS of an edge count as inside."""
    inside = False
    for i in range(len(polygon)):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % len(polygon)]
        if _on_segment(x, y, x1, y1, x2, y2):
            return True
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def compute_ratsnest(board: Board) -> RatsnestReport:
    reports: list[NetReport] = []
    for net_code, net_name in sorted(board.nets.items()):
        if net_code == 0:
            continue  # net 0 is KiCad's "no net"
        report = _compute_net(board, net_code, net_name)
        if report is not None:
            reports.append(report)
    return RatsnestReport(nets=reports)


def _compute_net(board: Board, net_code: int, net_name: str) -> NetReport | None:
    pads = [p for p in board.pads if p.net_code == net_code]
    segments = [s for s in board.segments if s.net_code == net_code]
    vias = [v for v in board.vias if v.net_code == net_code]
    zones = [z for z in board.zones if z.net_code == net_code and z.polygons]
    if not pads and not segments:
        return None

    # Item list: pads first, then one junction item per distinct copper point.
    points: list[tuple[float, float]] = [(p.x, p.y) for p in pads]
    junction_index: dict[tuple[float, float], int] = {}

    def junction(x: float, y: float) -> int:
        key = (round(x, _COORD_KEY_DECIMALS), round(y, _COORD_KEY_DECIMALS))
        if key not in junction_index:
            junction_index[key] = len(points)
            points.append((x, y))
        return junction_index[key]

    segment_ends = [(junction(s.x1, s.y1), junction(s.x2, s.y2)) for s in segments]
    for v in vias:
        junction(v.x, v.y)

    # One item per zone, positioned at its first vertex (only used if it connects nothing).
    zone_start = len(points)
    for z in zones:
        points.append(z.polygons[0][0])

    uf = _UnionFind(len(points))
    for a, b in segment_ends:
        uf.union(a, b)
    for i, pad in enumerate(pads):
        for key, j in junction_index.items():
            if math.hypot(key[0] - pad.x, key[1] - pad.y) <= pad.radius + 1e-6:
                uf.union(i, j)
    for zi, z in enumerate(zones):
        for i in range(zone_start):
            x, y = points[i]
            if any(_point_in_polygon(x, y, poly) for poly in z.polygons):
                uf.union(zone_start + zi, i)

    clusters: dict[int, list[int]] = {}
    for i in range(len(points)):
        clusters.setdefault(uf.find(i), []).append(i)
    cluster_list = list(clusters.values())

    airwires = _mst_airwires(points, cluster_list, net_code, net_name)
    return NetReport(
        net_code=net_code,
        net_name=net_name,
        pad_count=len(pads),
        cluster_count=len(cluster_list),
        routed_length=sum(s.length for s in segments),
        airwires=airwires,
    )


def _mst_airwires(
    points: list[tuple[float, float]],
    clusters: list[list[int]],
    net_code: int,
    net_name: str,
) -> list[Airwire]:
    """Prim's MST over clusters; edge weight is the closest item pair."""
    if len(clusters) <= 1:
        return []

    def closest_pair(ca: list[int], cb: list[int]) -> tuple[float, int, int]:
        best = (math.inf, ca[0], cb[0])
        for i in ca:
            xi, yi = points[i]
            for j in cb:
                d = math.hypot(points[j][0] - xi, points[j][1] - yi)
                if d < best[0]:
                    best = (d, i, j)
        return best

    airwires: list[Airwire] = []
    in_tree = {0}
    remaining = set(range(1, len(clusters)))
    while remaining:
        best_edge: tuple[float, int, int, int] | None = None
        for r in remaining:
            for t in in_tree:
                d, i, j = closest_pair(clusters[t], clusters[r])
                if best_edge is None or d < best_edge[0]:
                    best_edge = (d, i, j, r)
        assert best_edge is not None
        _, i, j, r = best_edge
        airwires.append(
            Airwire(
                net_code=net_code,
                net_name=net_name,
                x1=points[i][0],
                y1=points[i][1],
                x2=points[j][0],
                y2=points[j][1],
            )
        )
        in_tree.add(r)
        remaining.remove(r)
    return airwires
