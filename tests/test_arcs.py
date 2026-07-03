import math
from pathlib import Path

import pytest

from netlist_agent.kicad_pcb import ArcTrack, Board, Pad, parse_board
from netlist_agent.ratsnest import compute_ratsnest
from netlist_agent.svg_render import render_svg

# Quarter circle: center (100, 110), radius 10, from (100, 100) via 45° to (110, 110).
ARC_BOARD = """
(kicad_pcb (version 20221018) (generator test)
  (net 0 "")
  (net 1 "CURVY")
  (footprint "test:R" (at 100 100) (property "Reference" "R1")
    (pad "1" smd rect (at 0 0) (size 0.8 0.8) (net 1 "CURVY")))
  (footprint "test:R" (at 110 110) (property "Reference" "R2")
    (pad "1" smd rect (at 0 0) (size 0.8 0.8) (net 1 "CURVY")))
  (arc (start 100 100) (mid 107.0711 102.9289) (end 110 110) (width 0.25) (layer "F.Cu") (net 1))
)
"""


@pytest.fixture()
def board(tmp_path: Path) -> Board:
    path = tmp_path / "arc.kicad_pcb"
    path.write_text(ARC_BOARD.strip(), encoding="utf-8")
    return parse_board(path)


def test_arc_parses_with_geometry(board: Board) -> None:
    assert len(board.arcs) == 1
    arc = board.arcs[0]
    assert (arc.x1, arc.y1, arc.x2, arc.y2) == (100.0, 100.0, 110.0, 110.0)
    assert arc.layer == "F.Cu"
    assert arc.net_code == 1
    assert arc.width == pytest.approx(0.25)
    assert arc.radius == pytest.approx(10.0, abs=1e-3)
    assert arc.length == pytest.approx(math.pi * 10 / 2, abs=1e-3)  # quarter circle


def test_collinear_arc_falls_back_to_chords() -> None:
    arc = ArcTrack(x1=0.0, y1=0.0, xm=5.0, ym=0.0, x2=10.0, y2=0.0, width=0.25, layer="F.Cu", net_code=1)
    assert arc.length == pytest.approx(10.0)
    assert arc.radius == math.inf


def test_arc_closes_connectivity(board: Board) -> None:
    report = compute_ratsnest(board)
    net = next(n for n in report.nets if n.net_code == 1)
    assert net.fully_routed  # the arc is the only copper between the two pads
    assert net.routed_length == pytest.approx(math.pi * 10 / 2, abs=1e-3)


def test_arc_only_net_still_reported() -> None:
    b = Board(
        nets={0: "", 1: "A"},
        arcs=[ArcTrack(x1=0, y1=0, xm=5, ym=5, x2=10, y2=0, width=0.25, layer="F.Cu", net_code=1)],
    )
    report = compute_ratsnest(b)
    assert len(report.nets) == 1
    assert report.nets[0].fully_routed


def test_arc_renders_as_svg_path(board: Board, tmp_path: Path) -> None:
    out = tmp_path / "arc.svg"
    render_svg(board, compute_ratsnest(board), out)
    svg = out.read_text(encoding="utf-8")

    assert "<path d=" in svg
    assert "A 200.00 200.00" in svg  # radius 10 at scale 20
    assert 'data-net="CURVY"' in svg
    assert 'data-layer="F.Cu"' in svg


def test_degenerate_arc_renders_as_line(tmp_path: Path) -> None:
    b = Board(
        nets={0: "", 1: "A"},
        pads=[Pad(reference="R1", pad_name="1", x=0.0, y=0.0, net_code=1, net_name="A", radius=0.4)],
        arcs=[ArcTrack(x1=0, y1=0, xm=5, ym=0, x2=10, y2=0, width=0.25, layer="F.Cu", net_code=1)],
    )
    out = tmp_path / "degenerate.svg"
    render_svg(b, compute_ratsnest(b), out)
    svg = out.read_text(encoding="utf-8")
    assert "<path d=" not in svg
    assert svg.count("<line") == 1
