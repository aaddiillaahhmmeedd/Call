"""Export a board back to schematic-side formats: KiCad XML netlist and BOM CSV.

The exported netlist matches the shape ``pcb_extractors`` parses, so a board
round-trips cleanly through ERC: ``compare(extract(export(board)), board)``
returns no issues.
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from typing import Any

from .kicad_pcb import Board, Footprint


def _natural_key(reference: str) -> tuple[Any, ...]:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", reference))


def _components(board: Board) -> list[Footprint]:
    if board.footprints:
        components = board.footprints
    else:
        seen: dict[str, Footprint] = {}
        for pad in board.pads:
            if pad.reference and pad.reference != "?" and pad.reference not in seen:
                seen[pad.reference] = Footprint(reference=pad.reference, name="?")
        components = list(seen.values())
    return sorted(components, key=lambda fp: _natural_key(fp.reference))


def export_netlist_xml(board: Board) -> str:
    """KiCad-style XML netlist: components plus pad-level net membership."""
    root = ET.Element("export", version="E")

    components = ET.SubElement(root, "components")
    for footprint in _components(board):
        comp = ET.SubElement(components, "comp", ref=footprint.reference)
        if footprint.value:
            ET.SubElement(comp, "value").text = footprint.value
        if footprint.name and footprint.name != "?":
            ET.SubElement(comp, "footprint").text = footprint.name

    nets = ET.SubElement(root, "nets")
    for net_code, net_name in sorted(board.nets.items()):
        if net_code == 0:
            continue
        pads = [p for p in board.pads if p.net_code == net_code]
        if not pads:
            continue
        net = ET.SubElement(nets, "net", code=str(net_code), name=net_name)
        for pad in sorted(pads, key=lambda p: (_natural_key(p.reference), _natural_key(p.pad_name))):
            ET.SubElement(net, "node", ref=pad.reference, pin=pad.pad_name)

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def export_bom_csv(board: Board) -> str:
    """Grouped BOM: one row per (value, footprint), references naturally sorted."""
    groups: dict[tuple[str, str], list[str]] = {}
    ungrouped: list[Footprint] = []
    for footprint in _components(board):
        name = footprint.name if footprint.name != "?" else ""
        if footprint.value or name:
            groups.setdefault((footprint.value or "", name), []).append(footprint.reference)
        else:
            ungrouped.append(footprint)

    rows: list[tuple[str, str, str, int]] = []
    for (value, name), refs in groups.items():
        refs.sort(key=_natural_key)
        rows.append((" ".join(refs), value, name, len(refs)))
    rows.extend((fp.reference, "", "", 1) for fp in ungrouped)
    rows.sort(key=lambda row: _natural_key(row[0].split()[0]))

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Reference", "Value", "Footprint", "Quantity"])
    writer.writerows(rows)
    return out.getvalue()
