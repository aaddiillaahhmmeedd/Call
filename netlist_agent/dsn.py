"""Specctra DSN export for :class:`~netlist_agent.kicad_pcb.Board`.

Emits the interchange format consumed by external autorouters such as
freerouting. Coordinates are integer micrometres; the board model is y-down
while DSN's Y axis points up, so all emitted y coordinates are negated.
"""

from __future__ import annotations

import math
from pathlib import Path

from .kicad_pcb import Board, Footprint, Pad, outline_bbox

VIA_PADSTACK = "Via[0-1]_800:400_um"
VIA_DIAMETER_UM = 800  # copper annulus of the default via padstack
DEFAULT_WIDTH_UM = 250  # rule defaults when no "Default" net class exists
DEFAULT_CLEARANCE_UM = 200
FALLBACK_MARGIN_MM = 1.0  # boundary margin around copper when no outline exists


def _um(value_mm: float) -> int:
    """Millimetres -> integer micrometres."""
    return int(round(value_mm * 1000))


def _quote(name: str) -> str:
    """Quote a DSN identifier with the declared string_quote when required."""
    if not name or any(ch in name for ch in ' ()"'):
        return '"' + name.replace('"', "") + '"'
    return name


def _angle(degrees: float) -> str:
    return f"{degrees:g}"


def _copper_layers(board: Board) -> list[str]:
    """Copper layers used by tracks/zones, always including F.Cu and B.Cu.

    Ordered front to back: F.Cu, inner layers sorted by name, B.Cu.
    """
    present = {seg.layer for seg in board.segments} | {zone.layer for zone in board.zones}
    inner = sorted(layer for layer in present if layer.endswith(".Cu") and layer not in ("F.Cu", "B.Cu"))
    return ["F.Cu", *inner, "B.Cu"]


def _boundary(board: Board) -> tuple[int, int, int, int]:
    """Boundary rect in µm (y negated): outline bbox, else copper bbox + margin."""
    bbox = outline_bbox(board)
    if bbox is None:
        xs = [pad.x for pad in board.pads] + [via.x for via in board.vias]
        ys = [pad.y for pad in board.pads] + [via.y for via in board.vias]
        for seg in board.segments:
            xs += [seg.x1, seg.x2]
            ys += [seg.y1, seg.y2]
        for zone in board.zones:
            for polygon in zone.polygons:
                xs += [x for x, _ in polygon]
                ys += [y for _, y in polygon]
        if not xs:
            return 0, 0, 0, 0
        margin = FALLBACK_MARGIN_MM
        bbox = (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)
    min_x, min_y, max_x, max_y = bbox
    # Negating y swaps which board-model edge is the DSN minimum.
    return _um(min_x), -_um(max_y), _um(max_x), -_um(min_y)


def _footprints_by_name(board: Board) -> dict[str, list[Footprint]]:
    grouped: dict[str, list[Footprint]] = {}
    for footprint in board.footprints:
        grouped.setdefault(footprint.name, []).append(footprint)
    return grouped


def _pin_offset(pad: Pad, footprint: Footprint) -> tuple[float, float]:
    """Pad offset in footprint-local coordinates (inverse of _parse_pad's rotation)."""
    dx = pad.x - footprint.x
    dy = pad.y - footprint.y
    theta = math.radians(footprint.rotation)
    px = dx * math.cos(theta) - dy * math.sin(theta)
    py = dx * math.sin(theta) + dy * math.cos(theta)
    return px, py


def _padstack_name(pad: Pad) -> str:
    return f"Round[A]{_um(2 * pad.radius)}"


def _structure(board: Board, layers: list[str]) -> list[str]:
    width, clearance = DEFAULT_WIDTH_UM, DEFAULT_CLEARANCE_UM
    default = board.net_classes.get("Default")
    if default is not None:
        if default.trace_width is not None:
            width = _um(default.trace_width)
        if default.clearance is not None:
            clearance = _um(default.clearance)
    x1, y1, x2, y2 = _boundary(board)

    lines = ["  (structure"]
    lines += [f"    (layer {layer} (type signal))" for layer in layers]
    lines.append(f"    (boundary (rect pcb {x1} {y1} {x2} {y2}))")
    lines.append(f'    (via "{VIA_PADSTACK}")')
    lines.append(f"    (rule (width {width}) (clearance {clearance}))")
    lines.append("  )")
    return lines


def _placement(board: Board) -> list[str]:
    lines = ["  (placement"]
    grouped = _footprints_by_name(board)
    for name in sorted(grouped):
        lines.append(f"    (component {_quote(name)}")
        for fp in sorted(grouped[name], key=lambda f: f.reference):
            lines.append(
                f"      (place {_quote(fp.reference)} {_um(fp.x)} {-_um(fp.y)} front {_angle(fp.rotation)})"
            )
        lines.append("    )")
    lines.append("  )")
    return lines


def _library(board: Board, layers: list[str]) -> list[str]:
    pads_by_ref: dict[str, list[Pad]] = {}
    for pad in board.pads:
        pads_by_ref.setdefault(pad.reference, []).append(pad)

    lines = ["  (library"]
    grouped = _footprints_by_name(board)
    for name in sorted(grouped):
        # Footprints sharing a name share one image; derive it from the
        # instance with the lowest reference.
        rep = min(grouped[name], key=lambda f: f.reference)
        lines.append(f"    (image {_quote(name)}")
        for pad in sorted(pads_by_ref.get(rep.reference, []), key=lambda p: p.pad_name):
            px, py = _pin_offset(pad, rep)
            lines.append(f"      (pin {_padstack_name(pad)} {_quote(pad.pad_name)} {_um(px)} {-_um(py)})")
        lines.append("    )")

    for diameter in sorted({_um(2 * pad.radius) for pad in board.pads}):
        lines.append(f"    (padstack Round[A]{diameter}")
        lines += [f"      (shape (circle {layer} {diameter}))" for layer in layers]
        lines.append("      (attach off)")
        lines.append("    )")

    lines.append(f'    (padstack "{VIA_PADSTACK}"')
    lines += [f"      (shape (circle {layer} {VIA_DIAMETER_UM}))" for layer in layers]
    lines.append("      (attach off)")
    lines.append("    )")
    lines.append("  )")
    return lines


def _network(board: Board) -> list[str]:
    pins_by_net: dict[str, list[str]] = {}
    for pad in sorted(board.pads, key=lambda p: (p.reference, p.pad_name)):
        if pad.net_name:
            pins_by_net.setdefault(pad.net_name, []).append(f"{pad.reference}-{pad.pad_name}")

    lines = ["  (network"]
    for net_name in sorted(pins_by_net):
        lines.append(f"    (net {_quote(net_name)}")
        lines.append(f"      (pins {' '.join(pins_by_net[net_name])})")
        lines.append("    )")
    lines.append("  )")
    return lines


def _net_clause(board: Board, net_code: int) -> str:
    net_name = board.nets.get(net_code, "")
    return f" (net {_quote(net_name)})" if net_name else ""


def _wiring(board: Board) -> list[str]:
    lines = ["  (wiring"]
    for seg in sorted(board.segments, key=lambda s: (s.layer, s.net_code, s.x1, s.y1, s.x2, s.y2)):
        path = (
            f"(path {seg.layer} {_um(seg.width)}"
            f" {_um(seg.x1)} {-_um(seg.y1)} {_um(seg.x2)} {-_um(seg.y2)})"
        )
        lines.append(f"    (wire {path}{_net_clause(board, seg.net_code)} (type route))")
    for via in sorted(board.vias, key=lambda v: (v.x, v.y, v.net_code)):
        lines.append(
            f'    (via "{VIA_PADSTACK}" {_um(via.x)} {-_um(via.y)}{_net_clause(board, via.net_code)})'
        )
    lines.append("  )")
    return lines


def export_dsn(board: Board, name: str = "board") -> str:
    """Render *board* as a Specctra DSN document."""
    layers = _copper_layers(board)
    lines: list[str] = [
        f'(pcb "{name}"',
        "  (parser",
        '    (string_quote ")',
        "    (space_in_quoted_tokens on)",
        '    (host_cad "netlist-agent")',
        "  )",
        "  (resolution um 10)",
        "  (unit um)",
    ]
    lines += _structure(board, layers)
    lines += _placement(board)
    lines += _library(board, layers)
    lines += _network(board)
    lines += _wiring(board)
    lines.append(")")
    return "\n".join(lines) + "\n"


def write_dsn(board: Board, output: Path, name: str = "board") -> None:
    """Write *board* as a Specctra DSN file to *output*."""
    Path(output).write_text(export_dsn(board, name), encoding="utf-8")
