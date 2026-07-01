"""Grid autorouter v1: turn ratsnest airwires into single-layer tracks.

Algorithm, per airwire (nets iterated in report order):

1. Discretize the board bounding box (all pads/segments/vias, plus a 2 mm
   margin) into a uniform grid of ``grid`` mm cells.
2. Mark cells blocked by copper belonging to *other* nets — pads inflated by
   ``clearance + width/2`` past their radius, segments by their half-width
   plus the same inflation. Obstacle sets are computed lazily per net and
   cached, so a board with few airwires never rasterizes every net.
3. A* from the cell nearest one airwire end to the cell nearest the other:
   8-directional movement, diagonal cost sqrt(2), octile-distance heuristic.
   Start and goal cells are always traversable (they sit on same-net pads).
4. Collapse the cell path into collinear runs and emit one TrackSegment per
   run. New segments join the obstacle model so later airwires of other nets
   avoid them, while later same-net routes still pass through them freely.

Airwires with no path are reported in ``failed`` and routing continues.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .kicad_pcb import Board, Pad, TrackSegment
from .ratsnest import Airwire, RatsnestReport

_SQRT2 = math.sqrt(2)
_NEIGHBORS: tuple[tuple[int, int, float], ...] = (
    (1, 0, 1.0),
    (-1, 0, 1.0),
    (0, 1, 1.0),
    (0, -1, 1.0),
    (1, 1, _SQRT2),
    (1, -1, _SQRT2),
    (-1, 1, _SQRT2),
    (-1, -1, _SQRT2),
)
_MARGIN_MM = 2.0

Cell = tuple[int, int]


@dataclass(slots=True)
class RouteResult:
    segments: list[TrackSegment]
    routed: list[Airwire]
    failed: list[Airwire]

    def to_dict(self) -> dict[str, Any]:
        return {
            "airwires_routed": len(self.routed),
            "airwires_failed": len(self.failed),
            "segments_added": len(self.segments),
            "new_track_length_mm": round(sum(s.length for s in self.segments), 3),
        }


def _point_segment_distance(px: float, py: float, seg: TrackSegment) -> float:
    dx, dy = seg.x2 - seg.x1, seg.y2 - seg.y1
    length_sq = dx * dx + dy * dy
    if length_sq <= 0.0:
        return math.hypot(px - seg.x1, py - seg.y1)
    t = ((px - seg.x1) * dx + (py - seg.y1) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (seg.x1 + t * dx), py - (seg.y1 + t * dy))


@dataclass(slots=True)
class _Router:
    board: Board
    grid: float
    clearance: float
    layer: str
    width: float
    min_x: float = 0.0
    min_y: float = 0.0
    cols: int = 0
    rows: int = 0
    # Static obstacles per routed net, rasterized on first use.
    _static_blocked: dict[int, set[Cell]] = field(default_factory=dict)
    # (net_code, cells) for segments created during this routing run.
    _dynamic_blocked: list[tuple[int, set[Cell]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        xs: list[float] = []
        ys: list[float] = []
        for pad in self.board.pads:
            xs.append(pad.x)
            ys.append(pad.y)
        for seg in self.board.segments:
            xs.extend((seg.x1, seg.x2))
            ys.extend((seg.y1, seg.y2))
        for via in self.board.vias:
            xs.append(via.x)
            ys.append(via.y)
        if not xs:
            xs = ys = [0.0]
        self.min_x = min(xs) - _MARGIN_MM
        self.min_y = min(ys) - _MARGIN_MM
        self.cols = int(round((max(xs) + _MARGIN_MM - self.min_x) / self.grid)) + 1
        self.rows = int(round((max(ys) + _MARGIN_MM - self.min_y) / self.grid)) + 1

    @property
    def _inflation(self) -> float:
        return self.clearance + self.width / 2

    def cell(self, x: float, y: float) -> Cell:
        col = min(max(int(round((x - self.min_x) / self.grid)), 0), self.cols - 1)
        row = min(max(int(round((y - self.min_y) / self.grid)), 0), self.rows - 1)
        return col, row

    def point(self, cell: Cell) -> tuple[float, float]:
        return (
            round(self.min_x + cell[0] * self.grid, 4),
            round(self.min_y + cell[1] * self.grid, 4),
        )

    def _cells_near(self, x: float, y: float, reach: float) -> list[Cell]:
        c_lo = max(int(math.floor((x - reach - self.min_x) / self.grid)), 0)
        c_hi = min(int(math.ceil((x + reach - self.min_x) / self.grid)), self.cols - 1)
        r_lo = max(int(math.floor((y - reach - self.min_y) / self.grid)), 0)
        r_hi = min(int(math.ceil((y + reach - self.min_y) / self.grid)), self.rows - 1)
        return [(c, r) for c in range(c_lo, c_hi + 1) for r in range(r_lo, r_hi + 1)]

    def _pad_cells(self, pad: Pad) -> set[Cell]:
        reach = pad.radius + self._inflation
        return {
            cell
            for cell in self._cells_near(pad.x, pad.y, reach)
            if math.hypot(self.point(cell)[0] - pad.x, self.point(cell)[1] - pad.y) <= reach
        }

    def _segment_cells(self, seg: TrackSegment) -> set[Cell]:
        reach = seg.width / 2 + self._inflation
        cx = (seg.x1 + seg.x2) / 2
        cy = (seg.y1 + seg.y2) / 2
        half_span = math.hypot(seg.x2 - seg.x1, seg.y2 - seg.y1) / 2 + reach
        return {
            cell
            for cell in self._cells_near(cx, cy, half_span)
            if _point_segment_distance(*self.point(cell), seg) <= reach
        }

    def _static_for(self, net_code: int) -> set[Cell]:
        if net_code not in self._static_blocked:
            blocked: set[Cell] = set()
            for pad in self.board.pads:
                if pad.net_code != net_code:
                    blocked |= self._pad_cells(pad)
            for seg in self.board.segments:
                if seg.net_code != net_code:
                    blocked |= self._segment_cells(seg)
            self._static_blocked[net_code] = blocked
        return self._static_blocked[net_code]

    def _blocked_for(self, net_code: int) -> set[Cell]:
        blocked = set(self._static_for(net_code))
        for seg_net, cells in self._dynamic_blocked:
            if seg_net != net_code:
                blocked |= cells
        return blocked

    def add_segment(self, seg: TrackSegment) -> None:
        self._dynamic_blocked.append((seg.net_code, self._segment_cells(seg)))

    def astar(self, start: Cell, goal: Cell, blocked: set[Cell]) -> list[Cell] | None:
        """A* over the grid; start/goal are traversable even if blocked."""

        def heuristic(cell: Cell) -> float:
            dx, dy = abs(cell[0] - goal[0]), abs(cell[1] - goal[1])
            return max(dx, dy) + (_SQRT2 - 1.0) * min(dx, dy)

        open_heap: list[tuple[float, float, Cell]] = [(heuristic(start), 0.0, start)]
        g_score: dict[Cell, float] = {start: 0.0}
        came_from: dict[Cell, Cell] = {}
        closed: set[Cell] = set()

        while open_heap:
            _, g, current = heapq.heappop(open_heap)
            if current == goal:
                path = [current]
                while current in came_from:
                    current = came_from[current]
                    path.append(current)
                path.reverse()
                return path
            if current in closed:
                continue
            closed.add(current)
            col, row = current
            for dc, dr, cost in _NEIGHBORS:
                nc, nr = col + dc, row + dr
                if not (0 <= nc < self.cols and 0 <= nr < self.rows):
                    continue
                neighbor = (nc, nr)
                if neighbor in blocked and neighbor != goal and neighbor != start:
                    continue
                tentative = g + cost
                if tentative < g_score.get(neighbor, math.inf):
                    g_score[neighbor] = tentative
                    came_from[neighbor] = current
                    heapq.heappush(open_heap, (tentative + heuristic(neighbor), tentative, neighbor))
        return None

    def path_to_segments(self, path: list[Cell], net_code: int) -> list[TrackSegment]:
        """Collapse the cell path into collinear runs, one segment per run."""
        if len(path) < 2:
            return []
        corners: list[Cell] = [path[0]]
        for i in range(1, len(path) - 1):
            prev_dir = (path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1])
            next_dir = (path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1])
            if prev_dir != next_dir:
                corners.append(path[i])
        corners.append(path[-1])

        segments: list[TrackSegment] = []
        for a, b in zip(corners, corners[1:]):
            x1, y1 = self.point(a)
            x2, y2 = self.point(b)
            segments.append(
                TrackSegment(
                    x1=x1, y1=y1, x2=x2, y2=y2,
                    width=self.width, layer=self.layer, net_code=net_code,
                )
            )
        return segments


def route_board(
    board: Board,
    report: RatsnestReport,
    grid: float = 0.25,
    clearance: float = 0.2,
    layer: str = "F.Cu",
    width: float = 0.25,
) -> RouteResult:
    """Route every airwire in ``report`` on a single copper layer."""
    router = _Router(board=board, grid=grid, clearance=clearance, layer=layer, width=width)
    result = RouteResult(segments=[], routed=[], failed=[])

    for net in report.nets:
        for airwire in net.airwires:
            start = router.cell(airwire.x1, airwire.y1)
            goal = router.cell(airwire.x2, airwire.y2)
            path = router.astar(start, goal, router._blocked_for(airwire.net_code))
            if path is None:
                result.failed.append(airwire)
                continue
            for segment in router.path_to_segments(path, airwire.net_code):
                result.segments.append(segment)
                router.add_segment(segment)
            result.routed.append(airwire)

    return result


def _fmt(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def write_routed_board(source: Path, result: RouteResult, output: Path) -> None:
    """Insert the new segments into the source board text, before the final ``)``."""
    text = Path(source).read_text(encoding="utf-8", errors="ignore")
    close = text.rindex(")")
    lines = "".join(
        f"  (segment (start {_fmt(s.x1)} {_fmt(s.y1)}) (end {_fmt(s.x2)} {_fmt(s.y2)})"
        f' (width {_fmt(s.width)}) (layer "{s.layer}") (net {s.net_code}))\n'
        for s in result.segments
    )
    if close > 0 and text[close - 1] != "\n":
        lines = "\n" + lines
    Path(output).write_text(text[:close] + lines + text[close:], encoding="utf-8")
