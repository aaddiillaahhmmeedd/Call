"""Grid autorouter v2: turn ratsnest airwires into tracks on one or two layers.

Algorithm, per airwire (nets iterated in report order):

1. Discretize the board bounding box (all pads/segments/vias, plus a 2 mm
   margin) into a uniform grid of ``grid`` mm cells.
2. Mark cells blocked by copper belonging to *other* nets — pads inflated by
   ``clearance + width/2`` past their radius, segments by their half-width
   plus the same inflation, vias by their pad radius plus the same inflation.
   Obstacle sets are per layer: segments block only their own layer (in
   two-layer mode; single-layer mode keeps the v1 behavior of blocking
   regardless of layer), while pads and vias conservatively block every
   layer. Obstacle sets are computed lazily per net and cached, so a board
   with few airwires never rasterizes every net.
3. A* from the cell nearest one airwire end to the cell nearest the other,
   over (col, row, layer): 8-directional planar movement, diagonal cost
   sqrt(2), octile-distance heuristic on x/y (layer ignored). In two-layer
   mode a layer-switch move costing ``via_cost`` is also allowed, but only
   when the cell is free on BOTH layers — the via goes through the board.
   Start and goal cells are always traversable (they sit on same-net pads)
   and both live on the first layer.
4. Collapse each same-layer run of the path into collinear segments; a layer
   change emits a Via at that point and starts a new run on the new layer.
   New segments join the obstacle model on their own layer and new vias on
   all layers, so later airwires of other nets avoid them, while later
   same-net routes still pass through them freely.

Airwires with no path are reported in ``failed`` and routing continues —
unless ``rip_up_retries`` allows tearing out previously routed airwires that
block the corridor: the blocker's copper is removed, the failed airwire is
retried first, and the ripped airwires are re-queued. Only copper created
during this run is ever ripped, each airwire funds at most ``rip_up_retries``
rip-ups, and a global step bound guarantees termination.

``net_widths`` assigns per-net track widths (falling back to ``width``); a
net's width sets both its new segments and the clearance inflation used when
routing it or avoiding it.
"""

from __future__ import annotations

import heapq
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .kicad_pcb import Board, Pad, TrackSegment, Via
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
_VIA_SIZE = 0.8
_VIA_DRILL = 0.4
_ALL_LAYERS = -1  # sentinel layer index: copper that blocks every routing layer

Cell = tuple[int, int]
Node = tuple[int, int, int]  # col, row, layer index


@dataclass(slots=True)
class RouteResult:
    segments: list[TrackSegment]
    routed: list[Airwire]
    failed: list[Airwire]
    vias: list[Via] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "airwires_routed": len(self.routed),
            "airwires_failed": len(self.failed),
            "segments_added": len(self.segments),
            "vias_added": len(self.vias),
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
    width: float
    layers: tuple[str, ...]
    via_cost: float = 5.0
    # Per-layer segment blocking (two-layer mode); False keeps the v1 rule
    # that existing segments block regardless of their layer.
    per_layer: bool = False
    net_widths: dict[int, float] | None = None
    # Track width of the net currently being routed; drives clearance inflation.
    active_width: float = 0.0
    min_x: float = 0.0
    min_y: float = 0.0
    cols: int = 0
    rows: int = 0
    _layer_index: dict[str, int] = field(default_factory=dict)
    # Static obstacles per routed net (one cell set per layer), rasterized on first use.
    _static_blocked: dict[int, list[set[Cell]]] = field(default_factory=dict)
    # (owner, net_code, layer_index, cells) for copper created during this run;
    # layer_index is _ALL_LAYERS for vias, which pass through the whole board.
    # The owner tag lets rip-up remove one airwire's copper.
    _dynamic_blocked: list[tuple[int, int, int, set[Cell]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.active_width = self.width
        self._layer_index = {name: i for i, name in enumerate(self.layers)}
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

    def width_for(self, net_code: int) -> float:
        return (self.net_widths or {}).get(net_code, self.width)

    @property
    def _inflation(self) -> float:
        return self.clearance + self.active_width / 2

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

    def _disc_cells(self, x: float, y: float, reach: float) -> set[Cell]:
        return {
            cell
            for cell in self._cells_near(x, y, reach)
            if math.hypot(self.point(cell)[0] - x, self.point(cell)[1] - y) <= reach
        }

    def _pad_cells(self, pad: Pad) -> set[Cell]:
        return self._disc_cells(pad.x, pad.y, pad.radius + self._inflation)

    def _via_cells(self, x: float, y: float) -> set[Cell]:
        return self._disc_cells(x, y, _VIA_SIZE / 2 + self._inflation)

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

    def _static_for(self, net_code: int) -> list[set[Cell]]:
        if net_code not in self._static_blocked:
            blocked: list[set[Cell]] = [set() for _ in self.layers]
            for pad in self.board.pads:
                if pad.net_code != net_code:
                    cells = self._pad_cells(pad)  # no layer info on Pad: block all layers
                    for layer_cells in blocked:
                        layer_cells |= cells
            for seg in self.board.segments:
                if seg.net_code == net_code:
                    continue
                if self.per_layer:
                    idx = self._layer_index.get(seg.layer)
                    if idx is not None:  # copper on other layers blocks nothing
                        blocked[idx] |= self._segment_cells(seg)
                else:
                    blocked[0] |= self._segment_cells(seg)
            for via in self.board.vias:
                if via.net_code != net_code:
                    cells = self._via_cells(via.x, via.y)  # through the board: all layers
                    for layer_cells in blocked:
                        layer_cells |= cells
            self._static_blocked[net_code] = blocked
        return self._static_blocked[net_code]

    def _blocked_for(self, net_code: int) -> list[set[Cell]]:
        blocked = [set(cells) for cells in self._static_for(net_code)]
        for _owner, other_net, layer_idx, cells in self._dynamic_blocked:
            if other_net == net_code:
                continue
            if layer_idx == _ALL_LAYERS:
                for layer_cells in blocked:
                    layer_cells |= cells
            else:
                blocked[layer_idx] |= cells
        return blocked

    def add_segment(self, seg: TrackSegment, owner: int) -> None:
        self._dynamic_blocked.append(
            (owner, seg.net_code, self._layer_index[seg.layer], self._segment_cells(seg))
        )

    def add_via(self, via: Via, owner: int) -> None:
        self._dynamic_blocked.append(
            (owner, via.net_code, _ALL_LAYERS, self._via_cells(via.x, via.y))
        )

    def remove_owner(self, owner: int) -> None:
        self._dynamic_blocked = [e for e in self._dynamic_blocked if e[0] != owner]

    def owner_cells(self, owner: int) -> set[Cell]:
        cells: set[Cell] = set()
        for entry_owner, _net, _layer, entry_cells in self._dynamic_blocked:
            if entry_owner == owner:
                cells |= entry_cells
        return cells

    def astar(self, start: Cell, goal: Cell, blocked: list[set[Cell]]) -> list[Node] | None:
        """A* over (col, row, layer); start/goal cells are traversable even if blocked."""

        def heuristic(node: Node) -> float:
            dx, dy = abs(node[0] - goal[0]), abs(node[1] - goal[1])
            return max(dx, dy) + (_SQRT2 - 1.0) * min(dx, dy)

        start_node: Node = (*start, 0)
        goal_node: Node = (*goal, 0)
        open_heap: list[tuple[float, float, Node]] = [(heuristic(start_node), 0.0, start_node)]
        g_score: dict[Node, float] = {start_node: 0.0}
        came_from: dict[Node, Node] = {}
        closed: set[Node] = set()

        def push(neighbor: Node, tentative: float, current: Node) -> None:
            if tentative < g_score.get(neighbor, math.inf):
                g_score[neighbor] = tentative
                came_from[neighbor] = current
                heapq.heappush(open_heap, (tentative + heuristic(neighbor), tentative, neighbor))

        while open_heap:
            _, g, current = heapq.heappop(open_heap)
            if current == goal_node:
                path = [current]
                while current in came_from:
                    current = came_from[current]
                    path.append(current)
                path.reverse()
                return path
            if current in closed:
                continue
            closed.add(current)
            col, row, li = current
            for dc, dr, cost in _NEIGHBORS:
                nc, nr = col + dc, row + dr
                if not (0 <= nc < self.cols and 0 <= nr < self.rows):
                    continue
                cell = (nc, nr)
                if cell in blocked[li] and cell != goal and cell != start:
                    continue
                push((nc, nr, li), g + cost, current)
            # Layer switch: a via needs the cell free on BOTH layers.
            cell = (col, row)
            if cell not in blocked[li]:
                for lj in range(len(self.layers)):
                    if lj != li and cell not in blocked[lj]:
                        push((col, row, lj), g + self.via_cost, current)
        return None

    def _run_segments(self, run: list[Cell], layer_idx: int, net_code: int) -> list[TrackSegment]:
        """Collapse one same-layer cell run into collinear segments."""
        if len(run) < 2:
            return []
        corners: list[Cell] = [run[0]]
        for i in range(1, len(run) - 1):
            prev_dir = (run[i][0] - run[i - 1][0], run[i][1] - run[i - 1][1])
            next_dir = (run[i + 1][0] - run[i][0], run[i + 1][1] - run[i][1])
            if prev_dir != next_dir:
                corners.append(run[i])
        corners.append(run[-1])

        segments: list[TrackSegment] = []
        for a, b in zip(corners, corners[1:]):
            x1, y1 = self.point(a)
            x2, y2 = self.point(b)
            segments.append(
                TrackSegment(
                    x1=x1, y1=y1, x2=x2, y2=y2,
                    width=self.active_width, layer=self.layers[layer_idx], net_code=net_code,
                )
            )
        return segments

    def path_to_routes(self, path: list[Node], net_code: int) -> tuple[list[TrackSegment], list[Via]]:
        """Split the node path into per-layer runs; each layer change emits a via."""
        segments: list[TrackSegment] = []
        vias: list[Via] = []
        run: list[Cell] = [path[0][:2]]
        layer_idx = path[0][2]
        for node in path[1:]:
            if node[2] != layer_idx:
                x, y = self.point(node[:2])
                vias.append(Via(x=x, y=y, net_code=net_code))
                segments.extend(self._run_segments(run, layer_idx, net_code))
                run = [node[:2]]
                layer_idx = node[2]
            else:
                run.append(node[:2])
        segments.extend(self._run_segments(run, layer_idx, net_code))
        return segments, vias


def _cell_line_distance(cell: Cell, a: Cell, b: Cell) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(cell[0] - a[0], cell[1] - a[1])
    t = max(0.0, min(1.0, ((cell[0] - a[0]) * dx + (cell[1] - a[1]) * dy) / length_sq))
    return math.hypot(cell[0] - (a[0] + t * dx), cell[1] - (a[1] + t * dy))

_RIP_CORRIDOR_CELLS = 3.0  # copper within this many cells of the straight line "blocks" it


def route_board(
    board: Board,
    report: RatsnestReport,
    grid: float = 0.25,
    clearance: float = 0.2,
    layer: str = "F.Cu",
    width: float = 0.25,
    layers: tuple[str, str] | None = None,
    via_cost: float = 5.0,
    net_widths: dict[int, float] | None = None,
    rip_up_retries: int = 2,
) -> RouteResult:
    """Route every airwire in ``report``.

    With ``layers`` unset, route on the single copper layer ``layer``. With
    ``layers`` set (e.g. ``("F.Cu", "B.Cu")``), route across both layers,
    inserting vias where the path switches layers. ``net_widths`` overrides
    the track width per net_code; ``rip_up_retries`` bounds how many blocking
    airwires a failed airwire may rip up and reroute.
    """
    router = _Router(
        board=board,
        grid=grid,
        clearance=clearance,
        width=width,
        layers=layers if layers is not None else (layer,),
        via_cost=via_cost,
        per_layer=layers is not None,
        net_widths=net_widths,
    )

    airwires = [a for net in report.nets for a in net.airwires]
    pending: deque[Airwire] = deque(airwires)
    placed: dict[int, tuple[Airwire, list[TrackSegment], list[Via]]] = {}
    order: list[int] = []  # owners in routing order, for most-recent-first rip-up
    failed: list[Airwire] = []
    rip_budget = {id(a): rip_up_retries for a in airwires}
    next_owner = 0
    max_steps = max(1, len(airwires)) * (rip_up_retries + 2) * 4

    def rip_candidate(net_code: int, start: Cell, goal: Cell) -> int | None:
        fallback: int | None = None
        for owner in reversed(order):
            if placed[owner][0].net_code == net_code:
                continue
            if fallback is None:
                fallback = owner
            cells = router.owner_cells(owner)
            if any(_cell_line_distance(c, start, goal) <= _RIP_CORRIDOR_CELLS for c in cells):
                return owner
        return fallback

    steps = 0
    while pending:
        steps += 1
        airwire = pending.popleft()
        router.active_width = router.width_for(airwire.net_code)
        start = router.cell(airwire.x1, airwire.y1)
        goal = router.cell(airwire.x2, airwire.y2)
        path = router.astar(start, goal, router._blocked_for(airwire.net_code))

        if path is not None:
            segments, vias = router.path_to_routes(path, airwire.net_code)
            for segment in segments:
                router.add_segment(segment, next_owner)
            for via in vias:
                router.add_via(via, next_owner)
            placed[next_owner] = (airwire, segments, vias)
            order.append(next_owner)
            next_owner += 1
            continue

        blocker = rip_candidate(airwire.net_code, start, goal) if steps < max_steps else None
        if rip_budget.get(id(airwire), 0) > 0 and blocker is not None:
            rip_budget[id(airwire)] -= 1
            ripped_airwire = placed.pop(blocker)[0]
            order.remove(blocker)
            router.remove_owner(blocker)
            pending.appendleft(airwire)  # retry through the freed corridor first
            pending.append(ripped_airwire)
        else:
            failed.append(airwire)

    result = RouteResult(segments=[], routed=[], failed=failed, vias=[])
    for owner in order:
        airwire, segments, vias = placed[owner]
        result.routed.append(airwire)
        result.segments.extend(segments)
        result.vias.extend(vias)
    return result


def _fmt(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def write_routed_board(source: Path, result: RouteResult, output: Path) -> None:
    """Insert the new segments and vias into the source board text, before the final ``)``."""
    text = Path(source).read_text(encoding="utf-8", errors="ignore")
    close = text.rindex(")")
    lines = "".join(
        f"  (segment (start {_fmt(s.x1)} {_fmt(s.y1)}) (end {_fmt(s.x2)} {_fmt(s.y2)})"
        f' (width {_fmt(s.width)}) (layer "{s.layer}") (net {s.net_code}))\n'
        for s in result.segments
    ) + "".join(
        f"  (via (at {_fmt(v.x)} {_fmt(v.y)}) (size {_fmt(_VIA_SIZE)}) (drill {_fmt(_VIA_DRILL)})"
        f' (layers "F.Cu" "B.Cu") (net {v.net_code}))\n'
        for v in result.vias
    )
    if close > 0 and text[close - 1] != "\n":
        lines = "\n" + lines
    Path(output).write_text(text[:close] + lines + text[close:], encoding="utf-8")
