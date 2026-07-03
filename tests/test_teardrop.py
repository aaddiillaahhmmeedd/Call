from pathlib import Path

import pytest

from netlist_agent.kicad_pcb import Board, Pad, TrackSegment, parse_board
from netlist_agent.ratsnest import _point_in_polygon, compute_ratsnest
from netlist_agent.teardrop import generate_teardrops, teardrops_to_zones, write_teardropped_board


def _pad(ref: str, x: float, y: float, net_code: int, radius: float = 0.6) -> Pad:
    return Pad(reference=ref, pad_name="1", x=x, y=y, net_code=net_code, net_name="N", radius=radius)


def _seg(x1: float, y1: float, x2: float, y2: float, width: float = 0.25) -> TrackSegment:
    return TrackSegment(x1=x1, y1=y1, x2=x2, y2=y2, width=width, layer="F.Cu", net_code=1)


def test_teardrop_geometry() -> None:
    board = Board(
        nets={0: "", 1: "N"},
        pads=[_pad("R1", 100.0, 100.0, 1)],
        segments=[_seg(100.0, 100.0, 110.0, 100.0)],
    )
    teardrops = generate_teardrops(board)
    assert len(teardrops) == 1

    td = teardrops[0]
    assert td.layer == "F.Cu"
    base_a, base_b, apex = td.polygon
    assert apex == (pytest.approx(100.6), pytest.approx(100.0))  # length_ratio * radius
    assert sorted([base_a[1], base_b[1]]) == [pytest.approx(99.46), pytest.approx(100.54)]
    assert base_a[0] == base_b[0] == pytest.approx(100.0)

    # The wedge covers the pad-track junction.
    assert _point_in_polygon(100.3, 100.0, td.polygon)


def test_fat_track_and_detached_segment_skipped() -> None:
    board = Board(
        nets={0: "", 1: "N"},
        pads=[_pad("R1", 100.0, 100.0, 1)],
        segments=[
            _seg(100.0, 100.0, 110.0, 100.0, width=1.5),  # >= pad diameter
            _seg(103.0, 103.0, 110.0, 103.0),  # not touching the pad
        ],
    )
    assert generate_teardrops(board) == []


def test_short_segment_clamps_length() -> None:
    board = Board(
        nets={0: "", 1: "N"},
        pads=[_pad("R1", 100.0, 100.0, 1, radius=2.0)],
        segments=[_seg(100.0, 100.0, 102.4, 100.0)],
    )
    (td,) = generate_teardrops(board)
    assert td.polygon[2][0] == pytest.approx(101.92)  # 0.8 * 2.4 mm segment, not radius 2.0


def test_segment_fully_inside_pad_skipped() -> None:
    board = Board(
        nets={0: "", 1: "N"},
        pads=[_pad("R1", 100.0, 100.0, 1, radius=2.0)],
        segments=[_seg(100.0, 100.0, 101.0, 100.0)],
    )
    assert generate_teardrops(board) == []


def test_zone_grouping() -> None:
    board = Board(
        nets={0: "", 1: "N"},
        pads=[_pad("R1", 100.0, 100.0, 1), _pad("R2", 110.0, 100.0, 1)],
        segments=[_seg(100.0, 100.0, 110.0, 100.0)],
    )
    teardrops = generate_teardrops(board)
    assert len(teardrops) == 2  # one per pad, same segment
    zones = teardrops_to_zones(teardrops)
    assert len(zones) == 1
    assert len(zones[0].polygons) == 2
    assert zones[0].net_code == 1
    assert zones[0].layer == "F.Cu"


def test_write_teardropped_board(tmp_path: Path) -> None:
    source = tmp_path / "mini.kicad_pcb"
    source.write_text(
        '(kicad_pcb (version 20221018) (generator test)\n'
        '  (net 0 "")\n'
        '  (net 1 "N")\n'
        '  (footprint "test:R" (at 100 100) (property "Reference" "R1")\n'
        '    (pad "1" smd rect (at 0 0) (size 1.2 1.2) (net 1 "N")))\n'
        '  (footprint "test:R" (at 110 100) (property "Reference" "R2")\n'
        '    (pad "1" smd rect (at 0 0) (size 1.2 1.2) (net 1 "N")))\n'
        '  (segment (start 100 100) (end 110 100) (width 0.25) (layer "F.Cu") (net 1))\n'
        ')\n',
        encoding="utf-8",
    )
    board = parse_board(source)
    before = next(n for n in compute_ratsnest(board).nets if n.net_code == 1)
    assert before.fully_routed

    teardrops = generate_teardrops(board)
    assert teardrops
    output = tmp_path / "teardropped.kicad_pcb"
    write_teardropped_board(source, teardrops, output)

    dropped = parse_board(output)
    assert len(dropped.zones) == 1
    assert len(dropped.zones[0].polygons) == len(teardrops)
    after = next(n for n in compute_ratsnest(dropped).nets if n.net_code == 1)
    assert after.fully_routed  # connectivity unharmed
