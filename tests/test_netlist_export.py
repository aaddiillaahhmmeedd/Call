import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from netlist_agent.erc import compare
from netlist_agent.kicad_pcb import parse_board
from netlist_agent.netlist_export import export_bom_csv, export_netlist_xml
from netlist_agent.pcb_extractors import extract_from_file

BOARD = """
(kicad_pcb (version 20221018) (generator test)
  (net 0 "")
  (net 1 "A<&>B")
  (net 2 "GND")
  (footprint "Resistor_SMD:R_0603" (at 100 100)
    (property "Reference" "R1")
    (property "Value" "10k")
    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 1 "A<&>B"))
    (pad "2" smd rect (at 1 0) (size 0.8 0.8) (net 2 "GND"))
  )
  (footprint "Resistor_SMD:R_0603" (at 110 100)
    (property "Reference" "R10")
    (property "Value" "10k")
    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 1 "A<&>B"))
    (pad "2" smd rect (at 1 0) (size 0.8 0.8) (net 2 "GND"))
  )
  (footprint "Capacitor_SMD:C_0603" (at 120 100 90)
    (fp_text reference "R2" (at 0 -1))
    (fp_text value "1,5n" (at 0 1))
    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 2 "GND"))
  )
)
"""


@pytest.fixture()
def board_file(tmp_path: Path) -> Path:
    path = tmp_path / "export.kicad_pcb"
    path.write_text(BOARD.strip(), encoding="utf-8")
    return path


def test_footprint_metadata(board_file: Path) -> None:
    board = parse_board(board_file)
    by_ref = {fp.reference: fp for fp in board.footprints}

    assert set(by_ref) == {"R1", "R10", "R2"}
    assert by_ref["R1"].name == "Resistor_SMD:R_0603"
    assert by_ref["R1"].value == "10k"
    assert (by_ref["R1"].x, by_ref["R1"].y) == (100.0, 100.0)
    assert by_ref["R2"].value == "1,5n"  # legacy fp_text style
    assert by_ref["R2"].rotation == 90.0


def test_netlist_xml_structure(board_file: Path) -> None:
    board = parse_board(board_file)
    xml = export_netlist_xml(board)
    root = ET.fromstring(xml)

    refs = [comp.attrib["ref"] for comp in root.findall("./components/comp")]
    assert refs == ["R1", "R2", "R10"]  # natural sort: R2 before R10

    net = next(n for n in root.findall("./nets/net") if n.attrib["name"] == "A<&>B")
    nodes = [(node.attrib["ref"], node.attrib["pin"]) for node in net.findall("node")]
    assert nodes == [("R1", "1"), ("R10", "1")]


def test_erc_round_trip(board_file: Path, tmp_path: Path) -> None:
    board = parse_board(board_file)
    netlist_file = tmp_path / "exported.net"
    netlist_file.write_text(export_netlist_xml(board), encoding="utf-8")

    schematic = extract_from_file("local", netlist_file)
    assert schematic is not None
    assert compare(schematic, board) == []


def test_bom_grouping_and_quoting(board_file: Path) -> None:
    bom = export_bom_csv(parse_board(board_file))
    lines = bom.splitlines()

    assert lines[0] == "Reference,Value,Footprint,Quantity"
    assert "R1 R10,10k,Resistor_SMD:R_0603,2" in lines
    assert '"1,5n"' in bom  # comma in value gets quoted
