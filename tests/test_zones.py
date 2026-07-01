from pathlib import Path

import pytest

from netlist_agent.kicad_pcb import parse_board
from netlist_agent.ratsnest import compute_ratsnest
from netlist_agent.svg_render import render_svg

# Two nets:
#   N1 (R1.2 -> R2.1) is fully routed by one track and has no zones.
#   GND has three pads and no tracks; a filled F.Cu zone covers R1.1 and
#   R2.2 but not C1.1 -> exactly 1 airwire out to C1.1.
BOARD = """
(kicad_pcb (version 20221018) (generator test)
  (net 0 "")
  (net 1 "N1")
  (net 2 "GND")
  (footprint "R_0603" (at 100 100)
    (property "Reference" "R1" (at 0 -1))
    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 2 "GND"))
    (pad "2" smd rect (at 1 0) (size 0.8 0.8) (net 1 "N1"))
  )
  (footprint "R_0603" (at 110 100)
    (property "Reference" "R2" (at 0 -1))
    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 1 "N1"))
    (pad "2" smd rect (at 1 0) (size 0.8 0.8) (net 2 "GND"))
  )
  (footprint "C_0603" (at 130 130)
    (property "Reference" "C1" (at 0 -1))
    (pad "1" smd rect (at 0 0) (size 0.8 0.8) (net 2 "GND"))
  )
  (segment (start 101 100) (end 109 100) (width 0.25) (layer "F.Cu") (net 1))
  (zone (net 2) (net_name "GND") (layer "F.Cu") (hatch edge 0.5)
    (polygon (pts (xy 95 95) (xy 115 95) (xy 115 105) (xy 95 105)))
    (filled_polygon (layer "F.Cu")
      (pts (xy 96 96) (xy 114 96) (xy 114 104) (xy 96 104))
    )
  )
)
"""


@pytest.fixture()
def board_file(tmp_path: Path) -> Path:
    path = tmp_path / "zones.kicad_pcb"
    path.write_text(BOARD.strip(), encoding="utf-8")
    return path


def test_parse_zone(board_file: Path) -> None:
    board = parse_board(board_file)
    assert len(board.zones) == 1
    zone = board.zones[0]
    assert zone.net_code == 2
    assert zone.layer == "F.Cu"
    # filled_polygon is preferred over the outline (polygon ...).
    assert len(zone.polygons) == 1
    assert len(zone.polygons[0]) == 4
    assert zone.polygons[0][0] == (96.0, 96.0)


def test_parse_zone_outline_fallback(tmp_path: Path) -> None:
    text = (
        '(kicad_pcb (version 20221018) (generator test) (net 2 "GND")\n'
        '  (zone (net 2) (layer "B.Cu")\n'
        "    (polygon (pts (xy 0 0) (xy 10 0) (xy 10 10)))\n"
        "  )\n"
        ")"
    )
    path = tmp_path / "outline.kicad_pcb"
    path.write_text(text, encoding="utf-8")
    board = parse_board(path)
    assert board.zones[0].layer == "B.Cu"
    assert board.zones[0].polygons == [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]]


def test_zone_joins_pads(board_file: Path) -> None:
    report = compute_ratsnest(parse_board(board_file))
    by_name = {n.net_name: n for n in report.nets}

    gnd = by_name["GND"]
    assert gnd.pad_count == 3
    # Zone + the two pads inside it form one cluster; C1.1 is the other.
    assert gnd.cluster_count == 2
    assert len(gnd.airwires) == 1
    airwire = gnd.airwires[0]
    ends = {(airwire.x1, airwire.y1), (airwire.x2, airwire.y2)}
    assert (130.0, 130.0) in ends
    assert (111.0, 100.0) in ends  # closest in-zone pad, not the zone vertex


def test_net_without_zones_unaffected(board_file: Path) -> None:
    report = compute_ratsnest(parse_board(board_file))
    by_name = {n.net_name: n for n in report.nets}

    n1 = by_name["N1"]
    assert n1.fully_routed
    assert n1.cluster_count == 1
    assert n1.routed_length == pytest.approx(8.0)


def test_render_svg_zone_polygon(board_file: Path, tmp_path: Path) -> None:
    board = parse_board(board_file)
    report = compute_ratsnest(board)
    out = tmp_path / "zones.svg"
    render_svg(board, report, out)
    svg = out.read_text(encoding="utf-8")
    assert '<polygon points="' in svg
    assert 'fill="#c83434" fill-opacity="0.25"' in svg
    assert svg.index("<polygon") < svg.index("<line")  # zones render under everything else
