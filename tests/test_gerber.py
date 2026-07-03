import math
import re
from pathlib import Path

import pytest

from netlist_agent.gerber import export_drill, export_gerbers, export_layer, export_outline
from netlist_agent.kicad_pcb import ArcTrack, Board, Pad, TrackSegment, Via, Zone


def _board() -> Board:
    return Board(
        nets={0: "", 1: "N1", 2: "GND"},
        pads=[
            Pad(reference="R1", pad_name="1", x=100.0, y=100.0, net_code=1, net_name="N1", radius=0.4),
            Pad(reference="R2", pad_name="1", x=110.0, y=100.0, net_code=1, net_name="N1", radius=0.6),
        ],
        segments=[
            TrackSegment(x1=100.0, y1=100.0, x2=105.0, y2=100.0, width=0.25, layer="F.Cu", net_code=1),
            TrackSegment(x1=105.0, y1=100.0, x2=110.0, y2=100.0, width=0.5, layer="F.Cu", net_code=1),
            TrackSegment(x1=100.0, y1=102.0, x2=110.0, y2=102.0, width=0.25, layer="B.Cu", net_code=2),
        ],
        vias=[Via(x=105.0, y=100.0, net_code=1)],
        zones=[Zone(net_code=2, layer="F.Cu", polygons=[[(98.0, 98.0), (112.0, 98.0), (112.0, 104.0), (98.0, 104.0)]])],
    )


def test_layer_framing_and_apertures() -> None:
    text = export_layer(_board(), "F.Cu")
    lines = text.splitlines()

    assert lines[0] == "%FSLAX46Y46*%"
    assert lines[1] == "%MOMM*%"
    assert lines[-1] == "M02*"

    # Deterministic aperture numbering: 2 track widths, 2 pad diameters, 1 via.
    assert "%ADD10C,0.250*%" in text
    assert "%ADD11C,0.500*%" in text
    assert "%ADD12C,0.800*%" in text  # pad d=0.8
    assert "%ADD13C,1.200*%" in text  # pad d=1.2
    assert "%ADD14C,0.800*%" in text  # via aperture


def test_layer_filtering() -> None:
    f_cu = export_layer(_board(), "F.Cu")
    b_cu = export_layer(_board(), "B.Cu")

    # The B.Cu segment (y=102) draws only in the B.Cu file.
    assert f"X{100_000_000}Y{-102_000_000}D02*" not in f_cu
    assert f"X{100_000_000}Y{-102_000_000}D02*" in b_cu
    # The zone region renders only on F.Cu.
    assert "G36*" in f_cu and "G37*" in f_cu
    assert "G36*" not in b_cu


def test_coordinates_and_flashes() -> None:
    text = export_layer(_board(), "F.Cu")

    # 4.6 format: mm * 1e6, y negated (board y-down -> Gerber y-up).
    assert "X100000000Y-100000000D02*" in text
    assert "X105000000Y-100000000D01*" in text
    # 2 pad flashes + 1 via flash.
    assert text.count("D03*") == 3

    # Region outline closes back to its first vertex.
    region = text[text.index("G36*") : text.index("G37*")]
    assert region.count("D01*") == 4  # 3 remaining vertices + closing edge
    assert region.count("D02*") == 1


def test_drill_export() -> None:
    text = export_drill(_board())
    lines = text.splitlines()

    assert lines[0] == "M48"
    assert "METRIC,TZ" in lines
    assert "T1C0.400" in lines
    assert "X105.000Y-100.000" in lines
    assert lines[-1] == "M30"


def test_drill_export_no_vias() -> None:
    text = export_drill(Board(nets={0: ""}))
    assert "T1" not in text
    assert text.splitlines()[-1] == "M30"


def test_export_gerbers_writes_files(tmp_path: Path) -> None:
    written = export_gerbers(_board(), tmp_path / "gerbers")

    names = [p.name for p in written]
    assert names == sorted(names)
    assert set(names) == {"board-F_Cu.gbr", "board-B_Cu.gbr", "board-Edge_Cuts.gbr", "board-drill.drl"}
    for path in written:
        assert path.read_text(encoding="utf-8").strip()


def _edge(x1: float, y1: float, x2: float, y2: float) -> TrackSegment:
    return TrackSegment(x1=x1, y1=y1, x2=x2, y2=y2, width=0.0, layer="Edge.Cuts", net_code=0)


def test_outline_export() -> None:
    board = Board(
        nets={0: ""},
        edge_segments=[_edge(0.0, 0.0, 50.0, 0.0), _edge(50.0, 0.0, 50.0, 30.0)],
    )
    text = export_outline(board)
    lines = text.splitlines()

    assert lines[0] == "%FSLAX46Y46*%"
    assert lines[1] == "%MOMM*%"
    assert lines[-1] == "M02*"
    assert "%ADD10C,0.100*%" in text

    # One move/draw pair per edge segment, y negated.
    assert "X0Y0D02*" in text
    assert "X50000000Y0D01*" in text
    assert "X50000000Y0D02*" in text
    assert "X50000000Y-30000000D01*" in text
    assert text.count("D02*") == 2
    assert text.count("D01*") == 2


def test_outline_export_empty() -> None:
    text = export_outline(Board(nets={0: ""}))
    lines = text.splitlines()

    assert lines[0] == "%FSLAX46Y46*%"
    assert lines[1] == "%MOMM*%"
    assert lines[-1] == "M02*"
    assert "D01*" not in text and "D02*" not in text


# Quarter circle from tests/test_arcs.py: center (100, 110), radius 10, from
# (100, 100) via 45 degrees to (110, 110). The mid point (107.0711, 102.9289)
# is kept at full precision so the circumcenter lands exactly on (100, 110).
_MID_OFFSET = 10.0 * math.sqrt(0.5)
_QUARTER_ARC = ArcTrack(
    x1=100.0,
    y1=100.0,
    xm=100.0 + _MID_OFFSET,
    ym=110.0 - _MID_OFFSET,
    x2=110.0,
    y2=110.0,
    width=0.25,
    layer="F.Cu",
    net_code=1,
)


def test_arc_export_circular_interpolation() -> None:
    board = Board(nets={0: "", 1: "N1"}, arcs=[_QUARTER_ARC])
    text = export_layer(board, "F.Cu")
    lines = text.splitlines()

    # Arc widths join the track-width aperture pool.
    assert "%ADD10C,0.250*%" in text
    assert "G75*" in lines
    assert "G01*" in lines
    assert lines.index("G75*") < lines.index("G01*")
    assert f"{'X100000000Y-100000000'}D02*" in lines  # move to the arc start

    arc_lines = [line for line in lines if line.startswith(("G02", "G03"))]
    assert len(arc_lines) == 1
    match = re.fullmatch(r"(G0[23])X(-?\d+)Y(-?\d+)I(-?\d+)J(-?\d+)D01\*", arc_lines[0])
    assert match
    code = match.group(1)
    end_x, end_y, offset_i, offset_j = (int(match.group(n)) for n in range(2, 6))

    # Board-frame CCW sweep flips to clockwise (G02) under the y negation.
    assert code == "G02"
    assert (end_x, end_y) == (110_000_000, -110_000_000)
    assert (offset_i, offset_j) == (0, -10_000_000)  # center - start, 4.6 units, y negated

    # Semantics: sweeping from start to end around center in the emitted
    # direction must pass through the arc's mid point (in Gerber coords).
    sx, sy = 100.0, -100.0
    cx, cy = sx + offset_i / 1e6, sy + offset_j / 1e6
    radius = math.hypot(sx - cx, sy - cy)
    start_angle = math.atan2(sy - cy, sx - cx)
    end_angle = math.atan2(end_y / 1e6 - cy, end_x / 1e6 - cx)
    if code == "G03":  # counter-clockwise: increasing angle
        sweep = (end_angle - start_angle) % math.tau
    else:  # G02, clockwise: decreasing angle
        sweep = -((start_angle - end_angle) % math.tau)
    mid_angle = start_angle + sweep / 2
    assert cx + radius * math.cos(mid_angle) == pytest.approx(107.0711, abs=1e-3)
    assert cy + radius * math.sin(mid_angle) == pytest.approx(-102.9289, abs=1e-3)


def test_collinear_arc_falls_back_to_line() -> None:
    board = Board(
        nets={0: "", 1: "N1"},
        arcs=[ArcTrack(x1=0.0, y1=0.0, xm=5.0, ym=0.0, x2=10.0, y2=0.0, width=0.25, layer="F.Cu", net_code=1)],
    )
    text = export_layer(board, "F.Cu")

    assert "G02" not in text and "G03" not in text
    assert "X0Y0D02*" in text
    assert "X10000000Y0D01*" in text
