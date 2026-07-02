"""Parser for Eagle 6+ XML board files (``.brd``).

Produces the same :class:`~netlist_agent.kicad_pcb.Board` model as the KiCad
parser so the ratsnest engine works on Eagle boards unchanged.

Coordinate convention: Eagle's Y axis points up while our model (matching the
KiCad parser) is y-down, so every Y coordinate is negated on output.  Element
rotation ``R<deg>`` is counterclockwise in Eagle's y-up frame; ``MR<deg>``
additionally mirrors the part (local x is negated before rotating, and the
part sits on the back of the board).  After the y-negation this matches the
KiCad parser's pad transform: an element rotated R90 with a pad at local
(1, 0) lands at relative offset (0, -1) in y-down coordinates.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .kicad_pcb import Board, Pad, TrackSegment, Via, Zone

_ROT_RE = re.compile(r"^([SM]*)R(-?[0-9.]+)$")


@dataclass(slots=True)
class _PadDef:
    """A pad primitive inside a package, in package-local Eagle coordinates."""

    name: str
    x: float
    y: float
    radius: float


def _fattr(elem: ET.Element, name: str, default: float = 0.0) -> float:
    raw = elem.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_rot(rot: str | None) -> tuple[float, bool]:
    """Parse an Eagle rot attribute ("R90", "MR180", ...) -> (degrees, mirrored)."""
    if not rot:
        return 0.0, False
    match = _ROT_RE.match(rot.strip())
    if match is None:
        return 0.0, False
    return float(match.group(2)), "M" in match.group(1)


def _layer_name(layer: str | None) -> str:
    if layer == "1":
        return "F.Cu"
    if layer == "16":
        return "B.Cu"
    return str(layer or "")


def _package_pads(package: ET.Element) -> list[_PadDef]:
    pads: list[_PadDef] = []
    for smd in package.iter("smd"):
        dx = _fattr(smd, "dx")
        dy = _fattr(smd, "dy")
        pads.append(
            _PadDef(
                name=smd.get("name", "?"),
                x=_fattr(smd, "x"),
                y=_fattr(smd, "y"),
                radius=max(dx, dy, 0.2) / 2,
            )
        )
    for tho in package.iter("pad"):
        drill = _fattr(tho, "drill")
        diameter = _fattr(tho, "diameter")
        if diameter <= 0:
            diameter = 1.5 * drill  # Eagle's default annular ring heuristic
        pads.append(
            _PadDef(
                name=tho.get("name", "?"),
                x=_fattr(tho, "x"),
                y=_fattr(tho, "y"),
                radius=max(diameter, 0.2) / 2,
            )
        )
    return pads


def _element_pads(element: ET.Element, packages: dict[tuple[str, str], list[_PadDef]]) -> list[Pad]:
    reference = element.get("name", "?")
    ex = _fattr(element, "x")
    ey = _fattr(element, "y")
    rot_deg, mirrored = _parse_rot(element.get("rot"))
    theta = math.radians(rot_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    pads: list[Pad] = []
    for pd in packages.get((element.get("library", ""), element.get("package", "")), []):
        lx = -pd.x if mirrored else pd.x
        ax = ex + lx * cos_t - pd.y * sin_t  # CCW rotation in Eagle's y-up frame
        ay = ey + lx * sin_t + pd.y * cos_t
        pads.append(
            Pad(
                reference=reference,
                pad_name=pd.name,
                x=round(ax, 4),
                y=round(-ay, 4),  # Eagle is y-up; our model is y-down
                net_code=0,
                net_name="",
                radius=pd.radius,
            )
        )
    return pads


def _parse_signal(signal: ET.Element, net_code: int, board: Board, pad_lookup: dict[tuple[str, str], Pad]) -> None:
    net_name = signal.get("name", "")
    board.nets[net_code] = net_name

    for ref in signal.iter("contactref"):
        pad = pad_lookup.get((ref.get("element", ""), ref.get("pad", "")))
        if pad is not None:
            pad.net_code = net_code
            pad.net_name = net_name

    for wire in signal.iter("wire"):
        board.segments.append(
            TrackSegment(
                x1=_fattr(wire, "x1"),
                y1=-_fattr(wire, "y1"),
                x2=_fattr(wire, "x2"),
                y2=-_fattr(wire, "y2"),
                width=_fattr(wire, "width"),
                layer=_layer_name(wire.get("layer")),
                net_code=net_code,
            )
        )

    for via in signal.iter("via"):
        board.vias.append(Via(x=_fattr(via, "x"), y=-_fattr(via, "y"), net_code=net_code))

    for polygon in signal.iter("polygon"):
        vertices = [(_fattr(v, "x"), -_fattr(v, "y")) for v in polygon.iter("vertex")]
        if vertices:
            board.zones.append(
                Zone(net_code=net_code, layer=_layer_name(polygon.get("layer")), polygons=[vertices])
            )


def parse_eagle_board(path: Path | str) -> Board:
    try:
        root = ET.parse(str(path)).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"{path}: not an XML file") from exc
    board_el = root.find("drawing/board") if root.tag == "eagle" else None
    if board_el is None:
        raise ValueError(f"{path}: not an Eagle board file")

    board = Board(nets={0: ""})

    packages: dict[tuple[str, str], list[_PadDef]] = {}
    for library in board_el.iter("library"):
        lib_name = library.get("name", "")
        for package in library.iter("package"):
            packages[(lib_name, package.get("name", ""))] = _package_pads(package)

    pad_lookup: dict[tuple[str, str], Pad] = {}
    for element in board_el.iter("element"):
        for pad in _element_pads(element, packages):
            board.pads.append(pad)
            pad_lookup[(pad.reference, pad.pad_name)] = pad

    signals = board_el.find("signals")
    if signals is not None:
        for net_code, signal in enumerate(signals.iter("signal"), start=1):
            _parse_signal(signal, net_code, board, pad_lookup)

    return board


def is_eagle_board(path: Path | str) -> bool:
    """Cheap check: XML with an <eagle> root containing drawing/board."""
    seen_drawing = False
    try:
        for i, (_, elem) in enumerate(ET.iterparse(str(path), events=("start",))):
            if i == 0 and elem.tag != "eagle":
                return False
            if elem.tag == "drawing":
                seen_drawing = True
            elif elem.tag == "board" and seen_drawing:
                return True
            if i > 100_000:  # give up on huge non-board XML
                return False
    except (ET.ParseError, OSError):
        return False
    return False
