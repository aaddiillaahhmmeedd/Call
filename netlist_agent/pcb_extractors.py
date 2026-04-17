from __future__ import annotations

import csv
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import BoardNetlist, Component, NetConnection


NETLIST_EXTENSIONS = {".net", ".xml"}
BOM_HINTS = {"bom.csv", "ibom.csv", "components.csv"}


def extract_from_file(repo_name: str, file_path: Path) -> BoardNetlist | None:
    suffix = file_path.suffix.lower()
    name = file_path.name.lower()

    if suffix in NETLIST_EXTENSIONS:
        return _parse_kicad_xml_netlist(repo_name, file_path)
    if name in BOM_HINTS:
        return _parse_csv_bom(repo_name, file_path)
    return None


def _parse_kicad_xml_netlist(repo_name: str, file_path: Path) -> BoardNetlist | None:
    try:
        root = ET.parse(file_path).getroot()
    except ET.ParseError:
        return None

    components: list[Component] = []
    for comp in root.findall(".//components/comp"):
        components.append(
            Component(
                reference=comp.attrib.get("ref", ""),
                value=(comp.findtext("value") or "").strip() or None,
                footprint=(comp.findtext("footprint") or "").strip() or None,
                library=(comp.findtext("libsource") or "").strip() or None,
            )
        )

    nets: list[NetConnection] = []
    for net in root.findall(".//nets/net"):
        refs: list[str] = []
        for node in net.findall("node"):
            ref = node.attrib.get("ref")
            if ref:
                refs.append(ref)
        nets.append(NetConnection(net_name=net.attrib.get("name", ""), references=sorted(set(refs))))

    if not components and not nets:
        return None

    return BoardNetlist(repo=repo_name, path=str(file_path), components=components, nets=nets)


def _parse_csv_bom(repo_name: str, file_path: Path) -> BoardNetlist | None:
    with file_path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return None

        cols = {c.lower(): c for c in reader.fieldnames}
        ref_col = _best_col(cols, ["ref", "refs", "reference", "designator"])
        val_col = _best_col(cols, ["value", "val"])
        fp_col = _best_col(cols, ["footprint", "package"])

        if not ref_col:
            return None

        components: list[Component] = []
        for row in reader:
            raw_refs = row.get(ref_col) or ""
            refs = _explode_refs(raw_refs)
            for ref in refs:
                components.append(
                    Component(
                        reference=ref,
                        value=(row.get(val_col) if val_col else None),
                        footprint=(row.get(fp_col) if fp_col else None),
                        metadata={k: v for k, v in row.items() if v and k not in {ref_col, val_col, fp_col}},
                    )
                )

    if not components:
        return None

    return BoardNetlist(repo=repo_name, path=str(file_path), components=components, nets=[])


def _best_col(columns: dict[str, str], candidates: list[str]) -> str | None:
    for c in candidates:
        if c in columns:
            return columns[c]
    return None


def _explode_refs(raw_refs: str) -> list[str]:
    refs = [r.strip() for r in re.split(r"[,;\s]+", raw_refs) if r.strip()]
    return refs
