"""Tests for the Eagle 6+ XML board parser."""

from pathlib import Path

import pytest

from netlist_agent.eagle_brd import is_eagle_board, parse_eagle_board
from netlist_agent.kicad_pcb import Board, Pad, TrackSegment, Via, Zone
from netlist_agent.ratsnest import compute_ratsnest

EAGLE_BRD = """<?xml version="1.0" encoding="utf-8"?>
<eagle version="9.6.2">
<drawing>
<board>
<libraries>
  <library name="lib">
    <packages>
      <package name="pkg">
        <smd name="1" x="1" y="0" dx="1.2" dy="0.8" layer="1"/>
        <pad name="2" x="0" y="2" drill="0.8" diameter="1.6"/>
        <smd name="3" x="-1" y="0" dx="1" dy="1" layer="1"/>
      </package>
    </packages>
  </library>
</libraries>
<elements>
  <element name="R1" library="lib" package="pkg" x="10" y="10"/>
  <element name="R2" library="lib" package="pkg" x="20" y="10" rot="R90"/>
  <element name="R3" library="lib" package="pkg" x="5" y="5" rot="MR0"/>
</elements>
<signals>
  <signal name="GND">
    <contactref element="R1" pad="1"/>
    <contactref element="R2" pad="1"/>
    <wire x1="11" y1="10" x2="20" y2="11" width="0.25" layer="1"/>
    <via x="15" y="5" drill="0.4"/>
    <polygon width="0.2" layer="16">
      <vertex x="0" y="0"/>
      <vertex x="30" y="0"/>
      <vertex x="30" y="20"/>
      <vertex x="0" y="20"/>
    </polygon>
  </signal>
  <signal name="N$1">
    <contactref element="R1" pad="2"/>
    <contactref element="R2" pad="2"/>
  </signal>
</signals>
</board>
</drawing>
</eagle>
"""

KICAD_PCB = "(kicad_pcb (version 20221018) (net 0 \"\"))\n"


@pytest.fixture()
def brd_path(tmp_path: Path) -> Path:
    path = tmp_path / "demo.brd"
    path.write_text(EAGLE_BRD, encoding="utf-8")
    return path


@pytest.fixture()
def board(brd_path: Path) -> Board:
    return parse_eagle_board(brd_path)


def _pad(board: Board, reference: str, pad_name: str) -> Pad:
    for pad in board.pads:
        if pad.reference == reference and pad.pad_name == pad_name:
            return pad
    raise AssertionError(f"pad {reference}.{pad_name} not found")


def test_nets_assigned_in_document_order(board: Board) -> None:
    assert board.nets == {0: "", 1: "GND", 2: "N$1"}


def test_plain_element_pad_positions_and_y_negation(board: Board) -> None:
    pad1 = _pad(board, "R1", "1")
    assert (pad1.x, pad1.y) == (11.0, -10.0)
    assert pad1.net_code == 1 and pad1.net_name == "GND"
    assert pad1.radius == pytest.approx(0.6)  # max(dx, dy) / 2

    # Through-hole pad at local Eagle y=+2 lands at y offset -2 (y-down output).
    pad2 = _pad(board, "R1", "2")
    assert (pad2.x, pad2.y) == (10.0, -12.0)
    assert pad2.net_code == 2 and pad2.net_name == "N$1"
    assert pad2.radius == pytest.approx(0.8)  # diameter / 2


def test_r90_element_matches_kicad_rotation_convention(board: Board) -> None:
    # Element at (20, 10) rot=R90; pad at local (1, 0) must land at relative
    # offset (0, -1) in y-down coordinates, i.e. Eagle (20, 11) -> (20, -11).
    pad1 = _pad(board, "R2", "1")
    assert (pad1.x, pad1.y) == (20.0, -11.0)

    # Pad at local (0, 2): R90 CCW -> Eagle (-2, 0) offset -> abs (18, -10).
    pad2 = _pad(board, "R2", "2")
    assert (pad2.x, pad2.y) == (18.0, -10.0)


def test_mirrored_element_negates_local_x(board: Board) -> None:
    # R3 at (5, 5) rot=MR0: pad at local (1, 0) mirrors to (-1, 0) -> (4, -5).
    pad1 = _pad(board, "R3", "1")
    assert (pad1.x, pad1.y) == (4.0, -5.0)


def test_pads_without_contactref_get_net_zero(board: Board) -> None:
    pad3 = _pad(board, "R1", "3")
    assert pad3.net_code == 0 and pad3.net_name == ""


def test_wire_becomes_track_segment_with_mapped_layer(board: Board) -> None:
    assert len(board.segments) == 1
    seg = board.segments[0]
    assert isinstance(seg, TrackSegment)
    assert (seg.x1, seg.y1, seg.x2, seg.y2) == (11.0, -10.0, 20.0, -11.0)
    assert seg.width == pytest.approx(0.25)
    assert seg.layer == "F.Cu"
    assert seg.net_code == 1


def test_via_parsed_with_negated_y(board: Board) -> None:
    assert len(board.vias) == 1
    via = board.vias[0]
    assert isinstance(via, Via)
    assert (via.x, via.y) == (15.0, -5.0)
    assert via.net_code == 1


def test_polygon_becomes_zone_with_negated_y_vertices(board: Board) -> None:
    assert len(board.zones) == 1
    zone = board.zones[0]
    assert isinstance(zone, Zone)
    assert zone.net_code == 1
    assert zone.layer == "B.Cu"
    assert zone.polygons == [[(0.0, 0.0), (30.0, 0.0), (30.0, -20.0), (0.0, -20.0)]]


def test_ratsnest_runs_on_eagle_board(board: Board) -> None:
    report = compute_ratsnest(board)
    by_name = {net.net_name: net for net in report.nets}
    assert set(by_name) == {"GND", "N$1"}

    # GND: two pads joined by the routed wire -> fewer clusters than pads.
    gnd = by_name["GND"]
    assert gnd.pad_count == 2
    assert gnd.cluster_count < gnd.pad_count
    assert gnd.fully_routed

    # N$1: two pads, no copper -> two clusters, one airwire.
    n1 = by_name["N$1"]
    assert n1.pad_count == 2
    assert n1.cluster_count == 2
    assert len(n1.airwires) == 1

    summary = report.to_dict()
    assert summary["nets_total"] == 2
    assert summary["airwire_count"] == 1


def test_is_eagle_board_true(brd_path: Path) -> None:
    assert is_eagle_board(brd_path)


def test_is_eagle_board_false_for_kicad_and_other_xml(tmp_path: Path) -> None:
    kicad = tmp_path / "demo.kicad_pcb"
    kicad.write_text(KICAD_PCB, encoding="utf-8")
    assert not is_eagle_board(kicad)

    other = tmp_path / "other.xml"
    other.write_text("<svg><rect/></svg>", encoding="utf-8")
    assert not is_eagle_board(other)

    assert not is_eagle_board(tmp_path / "missing.brd")


def test_parse_rejects_non_eagle_files(tmp_path: Path) -> None:
    kicad = tmp_path / "demo.kicad_pcb"
    kicad.write_text(KICAD_PCB, encoding="utf-8")
    with pytest.raises(ValueError):
        parse_eagle_board(kicad)
