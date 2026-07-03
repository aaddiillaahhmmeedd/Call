"""Component placement optimizer: simulated annealing over footprint positions.

Minimizes total ratsnest length — the sum over nets of the minimum spanning
tree length over that net's pad positions — plus quadratic penalties when two
components' courtyard rectangles overlap or a rectangle leaves the board
outline (the ``Edge.Cuts`` bounding box, when the board has one). Components
named in ``fixed`` never move. Existing tracks and zones are ignored: this is
a pre-route optimizer.

Each component is modelled as a rigid cluster of pads. ``Board`` does not
carry footprint origins, so a component's position is its pads' centroid and
pad offsets are fixed relative to that centroid. ``write_placed_board``
translates each footprint's first ``(at x y [rot])`` by the centroid delta,
preserving rotation and leaving every other byte of the file untouched.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .kicad_pcb import (
    Board,
    Footprint,
    _footprint_reference,
    outline_bbox,
    parse_board,
    parse_sexpr,
)


def courtyard_extents(footprint: Footprint) -> tuple[float, float] | None:
    """Board-frame half-extents of the footprint's courtyard, or None.

    The local courtyard rectangle is rotated by the footprint angle and
    re-boxed: |w cos| + |h sin| per axis.
    """
    if footprint.courtyard_half_w is None or footprint.courtyard_half_h is None:
        return None
    theta = math.radians(footprint.rotation)
    w, h = footprint.courtyard_half_w, footprint.courtyard_half_h
    return (
        abs(w * math.cos(theta)) + abs(h * math.sin(theta)),
        abs(w * math.sin(theta)) + abs(h * math.cos(theta)),
    )


_GRID = 0.05  # placement grid in mm
_OVERLAP_WEIGHT = 100.0  # per mm^2 of courtyard overlap / outline excursion
_MOVE_HI = 10.0  # initial translation magnitude in mm
_MOVE_LO = 0.1  # final translation magnitude in mm
_SWAP_PROB = 0.15  # chance a move is a component pair swap
_COOL_END = 0.01  # final annealing temperature


@dataclass(slots=True)
class PlacementResult:
    positions: dict[str, tuple[float, float]]  # reference -> new footprint origin
    initial_length: float  # total airwire MST length before
    final_length: float  # after
    iterations: int

    def to_dict(self) -> dict[str, Any]:
        if self.initial_length > 0:
            improvement = 100 * (self.initial_length - self.final_length) / self.initial_length
        else:
            improvement = 0.0
        return {
            "initial_length_mm": round(self.initial_length, 3),
            "final_length_mm": round(self.final_length, 3),
            "improvement_pct": round(improvement, 1),
            "iterations": self.iterations,
            "positions": {ref: [round(x, 3), round(y, 3)] for ref, (x, y) in sorted(self.positions.items())},
        }


@dataclass(slots=True)
class _Component:
    reference: str
    offsets: list[tuple[float, float]]  # pad offsets from the centroid
    half_w: float  # courtyard half-extents: pad reach + pad radius + spacing, per axis
    half_h: float
    movable: bool
    nets: list[int] = field(default_factory=list)


def _snap(value: float) -> float:
    return round(round(value / _GRID) * _GRID, 4)


def _clamp(value: float, lo: float, hi: float) -> float:
    if lo > hi:  # rectangle wider than the board: settle for the middle
        return (lo + hi) / 2
    return min(max(value, lo), hi)


def _mst_length(points: list[tuple[float, float]]) -> float:
    """Prim's MST length over a small point set."""
    count = len(points)
    if count < 2:
        return 0.0
    dist_to = [math.inf] * count
    dist_to[0] = 0.0
    in_tree = [False] * count
    total = 0.0
    for _ in range(count):
        best = min((i for i in range(count) if not in_tree[i]), key=lambda i: dist_to[i])
        total += dist_to[best]
        in_tree[best] = True
        bx, by = points[best]
        for i in range(count):
            if not in_tree[i]:
                d = math.hypot(points[i][0] - bx, points[i][1] - by)
                if d < dist_to[i]:
                    dist_to[i] = d
    return total


def _build_components(
    board: Board, spacing: float
) -> tuple[list[_Component], list[tuple[float, float]], dict[int, list[tuple[int, float, float]]]]:
    by_ref: dict[str, list[Any]] = {}
    for pad in board.pads:
        by_ref.setdefault(pad.reference, []).append(pad)

    comps: list[_Component] = []
    positions: list[tuple[float, float]] = []
    net_pads: dict[int, list[tuple[int, float, float]]] = {}
    footprints = {fp.reference: fp for fp in board.footprints}
    for reference in sorted(by_ref):
        pads = by_ref[reference]
        cx = sum(p.x for p in pads) / len(pads)
        cy = sum(p.y for p in pads) / len(pads)
        offsets = [(p.x - cx, p.y - cy) for p in pads]
        pad_radius = max(p.radius for p in pads)
        # Real courtyard extents win over pad-derived ones; the courtyard is
        # measured from the footprint origin, close enough to the pad
        # centroid for rectangle purposes.
        courtyard = None
        if reference in footprints:
            courtyard = courtyard_extents(footprints[reference])
        if courtyard is not None:
            half_w, half_h = courtyard[0] + spacing, courtyard[1] + spacing
        else:
            half_w = max(abs(dx) for dx, _ in offsets) + pad_radius + spacing
            half_h = max(abs(dy) for _, dy in offsets) + pad_radius + spacing
        index = len(comps)
        comps.append(
            _Component(
                reference=reference,
                offsets=offsets,
                half_w=half_w,
                half_h=half_h,
                movable=any(p.net_code != 0 for p in pads),
            )
        )
        positions.append((cx, cy))
        for pad, (dx, dy) in zip(pads, offsets):
            if pad.net_code != 0:
                net_pads.setdefault(pad.net_code, []).append((index, dx, dy))

    net_pads = {net: entries for net, entries in net_pads.items() if len(entries) >= 2}
    for net, entries in sorted(net_pads.items()):
        for index, _, _ in entries:
            if net not in comps[index].nets:
                comps[index].nets.append(net)
    return comps, positions, net_pads


def _net_length(entries: list[tuple[int, float, float]], positions: list[tuple[float, float]]) -> float:
    return _mst_length([(positions[i][0] + dx, positions[i][1] + dy) for i, dx, dy in entries])


def _pair_penalty(positions: list[tuple[float, float]], comps: list[_Component], i: int, j: int) -> float:
    (xi, yi), (xj, yj) = positions[i], positions[j]
    x_overlap = comps[i].half_w + comps[j].half_w - abs(xj - xi)
    y_overlap = comps[i].half_h + comps[j].half_h - abs(yj - yi)
    return _OVERLAP_WEIGHT * x_overlap * y_overlap if x_overlap > 0 and y_overlap > 0 else 0.0


def _bounds_penalty(
    positions: list[tuple[float, float]],
    comps: list[_Component],
    i: int,
    bbox: tuple[float, float, float, float] | None,
) -> float:
    """Quadratic penalty for a component's rectangle poking past the board outline."""
    if bbox is None:
        return 0.0
    x, y = positions[i]
    comp = comps[i]
    ex = max(0.0, bbox[0] - (x - comp.half_w)) + max(0.0, (x + comp.half_w) - bbox[2])
    ey = max(0.0, bbox[1] - (y - comp.half_h)) + max(0.0, (y + comp.half_h) - bbox[3])
    return _OVERLAP_WEIGHT * (ex * ex + ey * ey)


def _local_penalty(
    positions: list[tuple[float, float]],
    comps: list[_Component],
    moved: tuple[int, ...],
    bbox: tuple[float, float, float, float] | None,
) -> float:
    """Overlap penalty over every pair touching a moved component (each pair once), plus outline penalties."""
    moved_set = set(moved)
    total = 0.0
    for i in moved:
        total += _bounds_penalty(positions, comps, i, bbox)
        for j in range(len(comps)):
            if j == i or (j in moved_set and j < i):
                continue
            total += _pair_penalty(positions, comps, i, j)
    return total


def optimize_placement(
    board: Board,
    iterations: int = 5000,
    seed: int = 0,
    spacing: float = 0.5,
    fixed: set[str] | frozenset[str] | None = None,
) -> PlacementResult:
    """Anneal component centroids to minimize total ratsnest MST length.

    Components whose reference appears in ``fixed`` keep their positions.
    """
    rng = random.Random(seed)
    fixed_refs = frozenset(fixed) if fixed else frozenset()
    comps, positions, net_pads = _build_components(board, spacing)
    movable = [i for i, c in enumerate(comps) if c.movable and c.reference not in fixed_refs]
    bbox = outline_bbox(board)

    net_len = {net: _net_length(entries, positions) for net, entries in net_pads.items()}
    initial_length = sum(net_len.values())
    if not movable or not net_pads:
        result_positions = {c.reference: positions[i] for i, c in enumerate(comps)}
        return PlacementResult(result_positions, initial_length, initial_length, 0)

    all_pairs = [(i, j) for i in range(len(comps)) for j in range(i + 1, len(comps))]
    total = initial_length + sum(_pair_penalty(positions, comps, i, j) for i, j in all_pairs)
    total += sum(_bounds_penalty(positions, comps, i, bbox) for i in range(len(comps)))
    best_total = total
    best_positions = list(positions)
    t_hi = max(1.0, 0.2 * initial_length)

    for step in range(iterations):
        frac = step / max(1, iterations - 1)
        temperature = t_hi * (_COOL_END / t_hi) ** frac
        magnitude = _MOVE_HI * (_MOVE_LO / _MOVE_HI) ** frac

        if len(movable) >= 2 and rng.random() < _SWAP_PROB:
            a, b = rng.sample(movable, 2)
            moved = (a, b)
            proposal = {a: positions[b], b: positions[a]}
        else:
            a = rng.choice(movable)
            x, y = positions[a]
            moved = (a,)
            nx = _snap(x + rng.uniform(-magnitude, magnitude))
            ny = _snap(y + rng.uniform(-magnitude, magnitude))
            if bbox is not None:  # penalties enforce the outline; clamping just converges faster
                nx = _clamp(nx, bbox[0] + comps[a].half_w, bbox[2] - comps[a].half_w)
                ny = _clamp(ny, bbox[1] + comps[a].half_h, bbox[3] - comps[a].half_h)
            proposal = {a: (nx, ny)}

        affected = sorted({net for i in moved for net in comps[i].nets})
        saved = {i: positions[i] for i in moved}
        old_cost = _local_penalty(positions, comps, moved, bbox) + sum(net_len[n] for n in affected)
        for i, xy in proposal.items():
            positions[i] = xy
        new_lens = {n: _net_length(net_pads[n], positions) for n in affected}
        delta = _local_penalty(positions, comps, moved, bbox) + sum(new_lens.values()) - old_cost

        if delta <= 0 or rng.random() < math.exp(-delta / temperature):
            total += delta
            net_len.update(new_lens)
            if total < best_total - 1e-9:
                best_total = total
                best_positions = list(positions)
        else:
            for i, xy in saved.items():
                positions[i] = xy

    final_length = sum(_net_length(entries, best_positions) for entries in net_pads.values())
    result_positions = {c.reference: best_positions[i] for i, c in enumerate(comps)}
    return PlacementResult(result_positions, initial_length, final_length, iterations)


def _skip_string(text: str, i: int) -> int:
    """``i`` sits on an opening quote; return the index just past the closing one."""
    i += 1
    while i < len(text) and text[i] != '"':
        i += 2 if text[i] == "\\" else 1
    return i + 1


def _balanced_end(text: str, start: int) -> int:
    """``start`` sits on ``(``; return the index just past the matching ``)``."""
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == '"':
            i = _skip_string(text, i)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _head_token(text: str, open_paren: int, end: int) -> tuple[str, int]:
    """Token following ``(`` at ``open_paren``; returns (token, index past token)."""
    j = open_paren + 1
    while j < end and text[j].isspace():
        j += 1
    k = j
    while k < end and not text[k].isspace() and text[k] not in '()"':
        k += 1
    return text[j:k], k


def _footprint_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i = _skip_string(text, i)
        elif ch == "(":
            token, _ = _head_token(text, i, n)
            if token in {"footprint", "module"}:
                end = _balanced_end(text, i)
                spans.append((i, end))
                i = end
            else:
                i += 1
        else:
            i += 1
    return spans


def _fmt(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _shift_first_at(text: str, start: int, end: int, delta: tuple[float, float]) -> tuple[int, int, str] | None:
    """Locate the block's first ``(at x y [rot])`` and build a shifted replacement.

    Returns (span_start, span_end, replacement) covering the expression's
    interior, or None if the block has no usable ``(at ...)``.
    """
    i = start + 1  # skip the footprint's own opening paren
    while i < end:
        ch = text[i]
        if ch == '"':
            i = _skip_string(text, i)
        elif ch == "(":
            token, after = _head_token(text, i, end)
            if token == "at":
                close = text.index(")", after)  # an (at ...) never nests
                fields = text[after:close].split()
                if len(fields) < 2:
                    return None
                x = float(fields[0]) + delta[0]
                y = float(fields[1]) + delta[1]
                replacement = " " + " ".join([_fmt(x), _fmt(y), *fields[2:]])
                return after, close, replacement
            i += 1
        else:
            i += 1
    return None


def write_placed_board(source: Path, result: PlacementResult, output: Path) -> None:
    """Copy ``source`` to ``output``, shifting each placed footprint's ``(at ...)``.

    Every footprint whose reference appears in ``result.positions`` gets its
    first ``(at x y [rot])`` translated by (new centroid - old centroid);
    rotation and all other text stay byte-identical.
    """
    text = Path(source).read_text(encoding="utf-8", errors="ignore")

    pads_by_ref: dict[str, list[Any]] = {}
    for pad in parse_board(source).pads:
        pads_by_ref.setdefault(pad.reference, []).append(pad)

    deltas: dict[str, tuple[float, float]] = {}
    for reference, (nx, ny) in result.positions.items():
        pads = pads_by_ref.get(reference)
        if not pads:
            continue
        dx = nx - sum(p.x for p in pads) / len(pads)
        dy = ny - sum(p.y for p in pads) / len(pads)
        if abs(dx) > 1e-9 or abs(dy) > 1e-9:
            deltas[reference] = (dx, dy)

    edits: list[tuple[int, int, str]] = []
    for start, end in _footprint_spans(text):
        delta = deltas.get(_footprint_reference(parse_sexpr(text[start:end])))
        if delta is not None and (edit := _shift_first_at(text, start, end, delta)):
            edits.append(edit)

    for span_start, span_end, replacement in sorted(edits, reverse=True):
        text = text[:span_start] + replacement + text[span_end:]
    Path(output).write_text(text, encoding="utf-8")
