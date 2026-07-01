import math
from pathlib import Path

import pytest

from netlist_agent.kicad_pcb import parse_board
from netlist_agent.ratsnest import compute_ratsnest
from netlist_agent.svg_render import render_svg

# Three parts, two nets:
#   N1 (R1.2 -> R2.1) is fully routed by one track.
#   GND (R1.1, R2.2, C1.1) has no copper -> needs 2 airwires.
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
  (footprint "C_0603" (at 105 110 90)
    (fp_text reference "C1" (at 0 -1))
    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 2 "GND"))
    (pad "2" smd rect (at 1 0) (size 0.8 0.8) (net 0 ""))
  )
  (segment (start 101 100) (end 109 100) (width 0.25) (layer "F.Cu") (net 1))
)
"""


@pytest.fixture()
def board_file(tmp_path: Path) -> Path:
    path = tmp_path / "demo.kicad_pcb"
    path.write_text(BOARD.strip(), encoding="utf-8")
    return path


def test_parse_board_pads_and_rotation(board_file: Path) -> None:
    board = parse_board(board_file)
    assert board.nets == {0: "", 1: "N1", 2: "GND"}

    pads = {(p.reference, p.pad_name): p for p in board.pads}
    assert pads[("R1", "1")].x == pytest.approx(99.0)
    assert pads[("R2", "2")].x == pytest.approx(111.0)

    # C1 is rotated 90 deg: pad offset (-1, 0) lands at (105, 111) in y-down coords.
    c1 = pads[("C1", "1")]
    assert (c1.x, c1.y) == (pytest.approx(105.0), pytest.approx(111.0))

    assert len(board.segments) == 1
    assert board.segments[0].length == pytest.approx(8.0)


def test_ratsnest_routed_and_unrouted(board_file: Path) -> None:
    report = compute_ratsnest(parse_board(board_file))
    by_name = {n.net_name: n for n in report.nets}

    n1 = by_name["N1"]
    assert n1.fully_routed
    assert n1.cluster_count == 1
    assert n1.routed_length == pytest.approx(8.0)

    gnd = by_name["GND"]
    assert not gnd.fully_routed
    assert gnd.pad_count == 3
    assert gnd.cluster_count == 3
    assert len(gnd.airwires) == 2
    # MST over (99,100), (111,100), (105,111): edges R1.1-R2.2 (12.0)
    # and the shorter diagonal to C1.1 (sqrt(36+121)).
    expected = 12.0 + math.hypot(6, 11)
    assert gnd.unrouted_length == pytest.approx(expected, abs=1e-6)

    summary = report.to_dict()
    assert summary["nets_total"] == 2
    assert summary["nets_fully_routed"] == 1
    assert summary["completion_pct"] == 50.0


def test_render_svg(board_file: Path, tmp_path: Path) -> None:
    board = parse_board(board_file)
    report = compute_ratsnest(board)
    out = tmp_path / "ratsnest.svg"
    render_svg(board, report, out)
    svg = out.read_text(encoding="utf-8")
    assert svg.startswith("<svg")
    assert svg.count("stroke-dasharray") == 2  # one dashed line per airwire
