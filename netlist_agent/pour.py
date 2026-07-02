"""Copper pour generation: fill a net's free board area with a zone.

Algorithm (``generate_pour``):

1. Rasterize the board bounding box — all pads, segment endpoints, and vias,
   expanded by ``margin`` — into ``grid`` mm cells.
2. A cell is copper-eligible unless it lies within ``clearance`` of foreign
   copper: pads of other nets (center distance <= radius + clearance),
   segments of other nets on the pour layer or with an unknown/empty layer
   (distance <= width/2 + clearance), and vias of other nets (distance <=
   via pad radius + clearance). Same-net copper never blocks.
3. Extract 4-connected regions of eligible cells and discard regions that
   contain no same-net copper (pad center, via, or segment endpoint cell) —
   an isolated pour island connects nothing. If no region qualifies, the
   largest region is kept anyway.
4. Emit each region as axis-aligned rectangles: maximal per-row cell runs,
   merged vertically while the column span repeats. Each rectangle (cell
   centers inflated by grid/2) covers only eligible cells, so the pour
   never claims copper of a foreign net.

Two optional refinements:

- ``thermal=True`` blocks the clearance ring around same-net pads too,
  except for four axis-aligned spoke corridors of ``spoke_width`` through
  the pad center, so pads connect to the pour through thermal reliefs
  rather than solid copper.
- ``smooth=True`` replaces the rectangle decomposition of a region with a
  single outer-boundary polygon traced along cell-square corners.
  ``Zone.polygons`` has no hole semantics, so a region that encloses
  ineligible cells (holes come precisely from foreign-copper clearance)
  keeps the rectangle decomposition instead — the pour must never claim
  foreign copper.

``write_poured_board`` inserts the zones into the source board text so that
``parse_board`` reads them back.
"""

from __future__ import annotations

import math
from collections import deque
from pathlib import Path

from .kicad_pcb import Board, TrackSegment, Zone

_VIA_PAD_RADIUS = 0.4

Cell = tuple[int, int]  # (col, row)
Span = tuple[int, int]  # (col_start, col_end), inclusive


def _point_segment_distance(px: float, py: float, seg: TrackSegment) -> float:
    dx, dy = seg.x2 - seg.x1, seg.y2 - seg.y1
    length_sq = dx * dx + dy * dy
    if length_sq <= 0.0:
        return math.hypot(px - seg.x1, py - seg.y1)
    t = ((px - seg.x1) * dx + (py - seg.y1) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (seg.x1 + t * dx), py - (seg.y1 + t * dy))


def _regions(cells: set[Cell]) -> list[set[Cell]]:
    """Split a cell set into 4-connected regions."""
    remaining = set(cells)
    regions: list[set[Cell]] = []
    while remaining:
        seed = remaining.pop()
        region = {seed}
        queue = deque([seed])
        while queue:
            col, row = queue.popleft()
            for neighbor in ((col + 1, row), (col - 1, row), (col, row + 1), (col, row - 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    region.add(neighbor)
                    queue.append(neighbor)
        regions.append(region)
    return regions


def _rectangles(region: set[Cell]) -> list[tuple[int, int, int, int]]:
    """Decompose a region into (col_start, col_end, row_start, row_end) blocks.

    Each row is split into maximal runs of consecutive columns; runs with an
    identical column span on consecutive rows merge vertically. The blocks
    are disjoint and cover exactly the region's cells.
    """
    columns: dict[int, list[int]] = {}
    for col, row in region:
        columns.setdefault(row, []).append(col)
    runs_by_row: dict[int, set[Span]] = {}
    for row, cols in columns.items():
        cols.sort()
        runs: set[Span] = set()
        start = prev = cols[0]
        for col in cols[1:]:
            if col != prev + 1:
                runs.add((start, prev))
                start = col
            prev = col
        runs.add((start, prev))
        runs_by_row[row] = runs

    rectangles: list[tuple[int, int, int, int]] = []
    open_blocks: dict[Span, int] = {}  # column span -> row it started on
    prev_row: int | None = None
    for row in sorted(runs_by_row):
        runs = runs_by_row[row]
        if prev_row is not None:
            contiguous = row == prev_row + 1
            for span, start_row in list(open_blocks.items()):
                if not contiguous or span not in runs:
                    rectangles.append((*span, start_row, prev_row))
                    del open_blocks[span]
        for span in runs:
            open_blocks.setdefault(span, row)
        prev_row = row
    for span, start_row in open_blocks.items():
        assert prev_row is not None
        rectangles.append((*span, start_row, prev_row))
    return rectangles


def _has_holes(region: set[Cell]) -> bool:
    """True when the region encloses cells that are not part of it."""
    cols = [c for c, _ in region]
    rows = [r for _, r in region]
    c_lo, c_hi = min(cols) - 1, max(cols) + 1
    r_lo, r_hi = min(rows) - 1, max(rows) + 1

    # Flood the complement from outside the (padded) bbox; unreached
    # complement cells are enclosed holes.
    seed = (c_lo, r_lo)
    visited = {seed}
    queue = deque([seed])
    while queue:
        col, row = queue.popleft()
        for neighbor in ((col + 1, row), (col - 1, row), (col, row + 1), (col, row - 1)):
            nc, nr = neighbor
            if c_lo <= nc <= c_hi and r_lo <= nr <= r_hi and neighbor not in region and neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    complement = (c_hi - c_lo + 1) * (r_hi - r_lo + 1) - len(region)
    return len(visited) < complement


def _trace_boundary(region: set[Cell]) -> list[tuple[int, int]] | None:
    """Outer boundary of a hole-free region as doubled cell coordinates.

    Each cell (c, r) is the square spanning doubled coords [2c-1, 2c+1] x
    [2r-1, 2r+1]. Directed boundary edges keep the region on one consistent
    side; chaining prefers the sharpest turn so pinch vertices stay on the
    current lobe. Returns None if the walk cannot consume every boundary
    edge in a single loop (fall back to rectangles then).
    """
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}
    count = 0
    for col, row in region:
        dc, dr = 2 * col, 2 * row
        sides = (
            ((col, row - 1), (dc - 1, dr - 1), (dc + 1, dr - 1)),  # top
            ((col + 1, row), (dc + 1, dr - 1), (dc + 1, dr + 1)),  # right
            ((col, row + 1), (dc + 1, dr + 1), (dc - 1, dr + 1)),  # bottom
            ((col - 1, row), (dc - 1, dr + 1), (dc - 1, dr - 1)),  # left
        )
        for neighbor, start, end in sides:
            if neighbor not in region:
                edges.setdefault(start, []).append(end)
                count += 1

    start = min(edges)
    loop = [start]
    current = start
    direction: tuple[int, int] | None = None
    consumed = 0
    while True:
        outs = edges.get(current, [])
        if not outs:
            return None
        if direction is None or len(outs) == 1:
            nxt = min(outs)
        else:
            dx, dy = direction
            preference = [(-dy, dx), (dx, dy), (dy, -dx)]  # sharpest turn first
            nxt = None
            for pdx, pdy in preference:
                candidate = (current[0] + 2 * pdx, current[1] + 2 * pdy)
                if candidate in outs:
                    nxt = candidate
                    break
            if nxt is None:
                return None
        outs.remove(nxt)
        consumed += 1
        direction = ((nxt[0] - current[0]) // 2, (nxt[1] - current[1]) // 2)
        current = nxt
        if current == start:
            break
        loop.append(current)
    if consumed != count:
        return None  # more than one boundary loop: pinched region

    simplified: list[tuple[int, int]] = []
    for i, vertex in enumerate(loop):
        before = loop[i - 1]
        after = loop[(i + 1) % len(loop)]
        if (vertex[0] - before[0], vertex[1] - before[1]) != (after[0] - vertex[0], after[1] - vertex[1]):
            simplified.append(vertex)
    return simplified


def generate_pour(
    board: Board,
    net_code: int,
    layer: str = "B.Cu",
    clearance: float = 0.3,
    grid: float = 0.25,
    margin: float = 1.0,
    thermal: bool = False,
    spoke_width: float = 0.5,
    smooth: bool = False,
) -> Zone:
    """Fill the net's free area on ``layer`` with rectilinear polygons."""
    xs: list[float] = []
    ys: list[float] = []
    for pad in board.pads:
        xs.append(pad.x)
        ys.append(pad.y)
    for seg in board.segments:
        xs.extend((seg.x1, seg.x2))
        ys.extend((seg.y1, seg.y2))
    for via in board.vias:
        xs.append(via.x)
        ys.append(via.y)
    if not xs:
        xs = ys = [0.0]
    min_x = min(xs) - margin
    min_y = min(ys) - margin
    cols = int(round((max(xs) + margin - min_x) / grid)) + 1
    rows = int(round((max(ys) + margin - min_y) / grid)) + 1

    foreign_pads = [p for p in board.pads if p.net_code != net_code]
    foreign_segments = [
        s for s in board.segments if s.net_code != net_code and s.layer in ("", layer)
    ]
    foreign_vias = [v for v in board.vias if v.net_code != net_code]
    thermal_pads = [p for p in board.pads if p.net_code == net_code] if thermal else []

    def eligible(col: int, row: int) -> bool:
        x = min_x + col * grid
        y = min_y + row * grid
        for pad in foreign_pads:
            if math.hypot(x - pad.x, y - pad.y) <= pad.radius + clearance:
                return False
        for seg in foreign_segments:
            if _point_segment_distance(x, y, seg) <= seg.width / 2 + clearance:
                return False
        for via in foreign_vias:
            if math.hypot(x - via.x, y - via.y) <= _VIA_PAD_RADIUS + clearance:
                return False
        for pad in thermal_pads:
            dx, dy = abs(x - pad.x), abs(y - pad.y)
            if (
                math.hypot(dx, dy) <= pad.radius + clearance
                and dx > spoke_width / 2
                and dy > spoke_width / 2
            ):
                return False  # clearance ring around the pad, minus the 4 spokes
        return True

    def cell(x: float, y: float) -> Cell:
        col = min(max(int(round((x - min_x) / grid)), 0), cols - 1)
        row = min(max(int(round((y - min_y) / grid)), 0), rows - 1)
        return col, row

    free = {(col, row) for col in range(cols) for row in range(rows) if eligible(col, row)}
    regions = _regions(free)

    # Same-net copper anchors: a region touching none of them is a useless island.
    anchors: set[Cell] = set()
    for pad in board.pads:
        if pad.net_code == net_code:
            anchors.add(cell(pad.x, pad.y))
    for seg in board.segments:
        if seg.net_code == net_code:
            anchors.add(cell(seg.x1, seg.y1))
            anchors.add(cell(seg.x2, seg.y2))
    for via in board.vias:
        if via.net_code == net_code:
            anchors.add(cell(via.x, via.y))

    kept = [region for region in regions if region & anchors]
    if not kept and regions:
        kept = [max(regions, key=len)]

    polygons: list[list[tuple[float, float]]] = []
    for region in kept:
        if smooth and not _has_holes(region):
            traced = _trace_boundary(region)
            if traced is not None:
                polygons.append(
                    [
                        (round(min_x + dc * grid / 2, 4), round(min_y + dr * grid / 2, 4))
                        for dc, dr in traced
                    ]
                )
                continue
        for c1, c2, r1, r2 in _rectangles(region):
            x_lo = round(min_x + c1 * grid - grid / 2, 4)
            x_hi = round(min_x + c2 * grid + grid / 2, 4)
            y_lo = round(min_y + r1 * grid - grid / 2, 4)
            y_hi = round(min_y + r2 * grid + grid / 2, 4)
            polygons.append([(x_lo, y_lo), (x_hi, y_lo), (x_hi, y_hi), (x_lo, y_hi)])
    return Zone(net_code=net_code, layer=layer, polygons=polygons)


def _fmt(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def write_poured_board(source: Path, zones: list[Zone], output: Path) -> None:
    """Insert the zones into the source board text, before the final ``)``."""
    text = Path(source).read_text(encoding="utf-8", errors="ignore")
    close = text.rindex(")")
    blocks: list[str] = []
    for zone in zones:
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
