"""Parser for KiCad ``.kicad_pcb`` board files (s-expression format).

Extracts the geometry needed for ratsnest computation: nets, pads with
absolute board coordinates, track segments, and vias.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SExpr = list[Any]  # nested lists of str


@dataclass(slots=True)
class Pad:
    reference: str
    pad_name: str
    x: float
    y: float
    net_code: int
    net_name: str
    radius: float  # half of the pad's largest dimension, used for hit-testing


@dataclass(slots=True)
class TrackSegment:
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    layer: str
    net_code: int

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)


@dataclass(slots=True)
class Via:
    x: float
    y: float
    net_code: int


@dataclass(slots=True)
class Zone:
    net_code: int
    layer: str
    polygons: list[list[tuple[float, float]]]


@dataclass(slots=True)
class NetClass:
    name: str
    clearance: float | None = None
    trace_width: float | None = None
    nets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Footprint:
    reference: str
    name: str
    value: str | None = None
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0


@dataclass(slots=True)
class Board:
    nets: dict[int, str] = field(default_factory=dict)
    pads: list[Pad] = field(default_factory=list)
    segments: list[TrackSegment] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    net_classes: dict[str, NetClass] = field(default_factory=dict)
    edge_segments: list[TrackSegment] = field(default_factory=list)  # board outline on Edge.Cuts
    footprints: list[Footprint] = field(default_factory=list)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "()":
            tokens.append(ch)
            i += 1
        elif ch == '"':
            j = i + 1
            buf: list[str] = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            tokens.append("".join(buf))
            i = j + 1
        elif ch.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def parse_sexpr(text: str) -> SExpr:
    tokens = tokenize(text)
    pos = 0

    def read() -> Any:
        nonlocal pos
        token = tokens[pos]
        pos += 1
        if token != "(":
            return token
        items: SExpr = []
        while pos < len(tokens) and tokens[pos] != ")":
            items.append(read())
        pos += 1  # consume ")"
        return items

    root = read()
    if not isinstance(root, list):
        raise ValueError("not an s-expression document")
    return root


def _children(expr: SExpr, tag: str) -> list[SExpr]:
    return [e for e in expr if isinstance(e, list) and e and e[0] == tag]


def _child(expr: SExpr, tag: str) -> SExpr | None:
    found = _children(expr, tag)
    return found[0] if found else None


def _floats(expr: SExpr | None, count: int) -> list[float]:
    if expr is None:
        return [0.0] * count
    values: list[float] = []
    for item in expr[1:]:
        if isinstance(item, str):
            try:
                values.append(float(item))
            except ValueError:
                continue
        if len(values) == count:
            break
    while len(values) < count:
        values.append(0.0)
    return values


def _footprint_reference(fp: SExpr) -> str:
    # KiCad 7+: (property "Reference" "R1" ...); older: (fp_text reference R1 ...)
    for prop in _children(fp, "property"):
        if len(prop) >= 3 and prop[1] == "Reference":
            return str(prop[2])
    for text in _children(fp, "fp_text"):
        if len(text) >= 3 and text[1] == "reference":
            return str(text[2])
    return "?"


def _footprint_value(fp: SExpr) -> str | None:
    for prop in _children(fp, "property"):
        if len(prop) >= 3 and prop[1] == "Value":
            return str(prop[2])
    for text in _children(fp, "fp_text"):
        if len(text) >= 3 and text[1] == "value":
            return str(text[2])
    return None


def _parse_pad(pad: SExpr, fp_x: float, fp_y: float, fp_rot: float, reference: str, nets: dict[int, str]) -> Pad | None:
    net = _child(pad, "net")
    if net is None or len(net) < 2:
        return None  # unconnected pad: not part of any ratsnest
    net_code = int(float(net[1]))
    px, py = _floats(_child(pad, "at"), 2)
    sx, sy = _floats(_child(pad, "size"), 2)
    theta = math.radians(fp_rot)
    x = fp_x + px * math.cos(theta) + py * math.sin(theta)
    y = fp_y - px * math.sin(theta) + py * math.cos(theta)
    return Pad(
        reference=reference,
        pad_name=str(pad[1]) if len(pad) > 1 else "?",
        x=round(x, 4),
        y=round(y, 4),
        net_code=net_code,
        net_name=nets.get(net_code, ""),
        radius=max(sx, sy, 0.2) / 2,
    )


def _polygon_points(expr: SExpr) -> list[tuple[float, float]]:
    pts = _child(expr, "pts")
    if pts is None:
        return []
    points: list[tuple[float, float]] = []
    for xy in _children(pts, "xy"):
        x, y = _floats(xy, 2)
        points.append((x, y))
    return points


def _parse_zone(zone: SExpr) -> Zone:
    net_expr = _child(zone, "net")
    layer_expr = _child(zone, "layer") or _child(zone, "layers")
    polygons = [pts for fp in _children(zone, "filled_polygon") if (pts := _polygon_points(fp))]
    if not polygons:
        outline = _child(zone, "polygon")
        if outline is not None and (pts := _polygon_points(outline)):
            polygons.append(pts)
    return Zone(
        net_code=int(float(net_expr[1])) if net_expr and len(net_expr) > 1 else 0,
        layer=str(layer_expr[1]) if layer_expr and len(layer_expr) > 1 else "",
        polygons=polygons,
    )


def _parse_net_class(expr: SExpr) -> NetClass:
    # (net_class "Power" "description" (clearance 0.3) (trace_width 0.5) (add_net "VCC") ...)
    # The name is the first string atom after the tag; the optional description
    # (second string atom) is skipped.
    atoms = [item for item in expr[1:] if isinstance(item, str)]
    clearance_expr = _child(expr, "clearance")
    trace_width_expr = _child(expr, "trace_width")
    return NetClass(
        name=atoms[0] if atoms else "?",
        clearance=_floats(clearance_expr, 1)[0] if clearance_expr else None,
        trace_width=_floats(trace_width_expr, 1)[0] if trace_width_expr else None,
        nets=[str(add[1]) for add in _children(expr, "add_net") if len(add) > 1],
    )


def _layer_name(expr: SExpr) -> str:
    layer_expr = _child(expr, "layer")
    return str(layer_expr[1]) if layer_expr and len(layer_expr) > 1 else ""


def _edge_segment(x1: float, y1: float, x2: float, y2: float) -> TrackSegment:
    return TrackSegment(x1=x1, y1=y1, x2=x2, y2=y2, width=0.0, layer="Edge.Cuts", net_code=0)


def outline_bbox(board: Board) -> tuple[float, float, float, float] | None:
    """Bounding box (min_x, min_y, max_x, max_y) of the board outline, or None without one."""
    if not board.edge_segments:
        return None
    xs = [x for seg in board.edge_segments for x in (seg.x1, seg.x2)]
    ys = [y for seg in board.edge_segments for y in (seg.y1, seg.y2)]
    return min(xs), min(ys), max(xs), max(ys)


def _plain_net_name(name: str) -> str:
    return name[1:] if name.startswith("/") else name


def net_class_for(board: Board, net_code: int) -> NetClass | None:
    """Net class whose member list contains the net's name, or None.

    Names are compared with a leading "/" stripped on both sides; an explicit
    (non-"Default") class wins over the "Default" class.
    """
    net_name = _plain_net_name(board.nets.get(net_code, ""))
    if not net_name:
        return None
    default: NetClass | None = None
    for net_class in board.net_classes.values():
        if any(_plain_net_name(member) == net_name for member in net_class.nets):
            if net_class.name == "Default":
                default = net_class
            else:
                return net_class
    return default


def width_for_net(board: Board, net_code: int, default: float = 0.25) -> float:
    """Trace width for the net: its class, else the "Default" class, else ``default``."""
    net_class = net_class_for(board, net_code)
    if net_class is not None and net_class.trace_width is not None:
        return net_class.trace_width
    fallback = board.net_classes.get("Default")
    if fallback is not None and fallback.trace_width is not None:
        return fallback.trace_width
    return default


def clearance_for_net(board: Board, net_code: int, default: float = 0.15) -> float:
    """Clearance for the net: its class, else the "Default" class, else ``default``."""
    net_class = net_class_for(board, net_code)
    if net_class is not None and net_class.clearance is not None:
        return net_class.clearance
    fallback = board.net_classes.get("Default")
    if fallback is not None and fallback.clearance is not None:
        return fallback.clearance
    return default


def parse_board(path: Path | str) -> Board:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    root = parse_sexpr(text)
    if not root or root[0] not in {"kicad_pcb", "pcbnew"}:
        raise ValueError(f"{path}: not a KiCad board file")

    board = Board()

    for net in _children(root, "net"):
        if len(net) >= 2:
            code = int(float(net[1]))
            board.nets[code] = str(net[2]) if len(net) > 2 else ""

    for tag in ("footprint", "module"):
        for fp in _children(root, tag):
            fp_x, fp_y, fp_rot = _floats(_child(fp, "at"), 3)
            reference = _footprint_reference(fp)
            name = next((item for item in fp[1:] if isinstance(item, str)), "?")
            board.footprints.append(
                Footprint(
                    reference=reference,
                    name=name,
                    value=_footprint_value(fp),
                    x=fp_x,
                    y=fp_y,
                    rotation=fp_rot,
                )
            )
            for pad_expr in _children(fp, "pad"):
                pad = _parse_pad(pad_expr, fp_x, fp_y, fp_rot, reference, board.nets)
                if pad is not None:
                    board.pads.append(pad)

    for seg in _children(root, "segment"):
        x1, y1 = _floats(_child(seg, "start"), 2)
        x2, y2 = _floats(_child(seg, "end"), 2)
        (width,) = _floats(_child(seg, "width"), 1)
        layer_expr = _child(seg, "layer")
        net_expr = _child(seg, "net")
        board.segments.append(
            TrackSegment(
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                width=width,
                layer=str(layer_expr[1]) if layer_expr and len(layer_expr) > 1 else "",
                net_code=int(float(net_expr[1])) if net_expr and len(net_expr) > 1 else 0,
            )
        )

    for via in _children(root, "via"):
        vx, vy = _floats(_child(via, "at"), 2)
        net_expr = _child(via, "net")
        board.vias.append(
            Via(x=vx, y=vy, net_code=int(float(net_expr[1])) if net_expr and len(net_expr) > 1 else 0)
        )

    for line in _children(root, "gr_line"):
        if _layer_name(line) != "Edge.Cuts":
            continue
        x1, y1 = _floats(_child(line, "start"), 2)
        x2, y2 = _floats(_child(line, "end"), 2)
        board.edge_segments.append(_edge_segment(x1, y1, x2, y2))

    for rect in _children(root, "gr_rect"):
        if _layer_name(rect) != "Edge.Cuts":
            continue
        x1, y1 = _floats(_child(rect, "start"), 2)
        x2, y2 = _floats(_child(rect, "end"), 2)
        board.edge_segments.extend(
            (
                _edge_segment(x1, y1, x2, y1),
                _edge_segment(x2, y1, x2, y2),
                _edge_segment(x2, y2, x1, y2),
                _edge_segment(x1, y2, x1, y1),
            )
        )

    for zone in _children(root, "zone"):
        board.zones.append(_parse_zone(zone))

    for net_class_expr in _children(root, "net_class"):
        net_class = _parse_net_class(net_class_expr)
        board.net_classes[net_class.name] = net_class

    return board
