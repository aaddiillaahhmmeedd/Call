import math
from pathlib import Path

import pytest

from netlist_agent.autoroute import RouteResult, route_board, write_routed_board
from netlist_agent.kicad_pcb import Board, Pad, TrackSegment, parse_board
from netlist_agent.ratsnest import compute_ratsnest

OBSTACLE = (105.0, 100.0)
OBSTACLE_RADIUS = 1.0

# A net-2 wall on F.Cu wide enough that its inflated footprint out-reaches the
# router's 2 mm bounding-box margin, so it blocks every row of the grid.
WALL_X = 105.0
WALL_WIDTH = 5.0
CLEARANCE = 0.2


def _pad(ref: str, x: float, y: float, net_code: int, net_name: str, radius: float = 0.4) -> Pad:
    return Pad(reference=ref, pad_name="1", x=x, y=y, net_code=net_code, net_name=net_name, radius=radius)


def _board() -> Board:
    return Board(
        nets={0: "", 1: "N1", 2: "GND"},
        pads=[
            _pad("R1", 100.0, 100.0, 1, "N1"),
            _pad("R2", 110.0, 100.0, 1, "N1"),
            _pad("U1", *OBSTACLE, 2, "GND", radius=OBSTACLE_RADIUS),
        ],
    )


def _wall_segments() -> list[TrackSegment]:
    return [
        TrackSegment(x1=WALL_X, y1=95.0, x2=WALL_X, y2=100.0, width=WALL_WIDTH, layer="F.Cu", net_code=2),
        TrackSegment(x1=WALL_X, y1=100.0, x2=WALL_X, y2=105.0, width=WALL_WIDTH, layer="F.Cu", net_code=2),
    ]


def _wall_board() -> Board:
    return Board(
        nets={0: "", 1: "N1", 2: "GND"},
        pads=[
            _pad("R1", 100.0, 100.0, 1, "N1"),
            _pad("R2", 110.0, 100.0, 1, "N1"),
        ],
        segments=_wall_segments(),
    )


def _point_segment_distance(px: float, py: float, seg: TrackSegment) -> float:
    dx, dy = seg.x2 - seg.x1, seg.y2 - seg.y1
    length_sq = dx * dx + dy * dy
    if length_sq <= 0.0:
        return math.hypot(px - seg.x1, py - seg.y1)
    t = max(0.0, min(1.0, ((px - seg.x1) * dx + (py - seg.y1) * dy) / length_sq))
    return math.hypot(px - (seg.x1 + t * dx), py - (seg.y1 + t * dy))


def test_route_around_obstacle() -> None:
    board = _board()
    report = compute_ratsnest(board)
    assert len(report.airwires) == 1  # sanity: one N1 airwire, GND is a single pad

    result = route_board(board, report)

    assert len(result.routed) == 1
    assert not result.failed
    assert result.segments

    # Every new segment keeps clear of the obstacle pad.
    for seg in result.segments:
        assert seg.net_code == 1
        assert seg.layer == "F.Cu"
        assert seg.width == pytest.approx(0.25)
        assert _point_segment_distance(*OBSTACLE, seg) > OBSTACLE_RADIUS

    # The detour is strictly longer than the 10 mm straight line.
    total = sum(s.length for s in result.segments)
    assert total > 10.0

    summary = result.to_dict()
    assert summary["airwires_routed"] == 1
    assert summary["airwires_failed"] == 0
    assert summary["new_track_length_mm"] == pytest.approx(total, abs=1e-3)

    # The new copper actually closes the connection.
    routed_board = _board()
    routed_board.segments = list(result.segments)
    after = compute_ratsnest(routed_board)
    n1 = next(n for n in after.nets if n.net_code == 1)
    assert n1.fully_routed
    assert not n1.airwires


def test_write_routed_board_parses(tmp_path: Path) -> None:
    source = tmp_path / "mini.kicad_pcb"
    source.write_text(
        '(kicad_pcb (version 20221018) (generator test)\n'
        '  (net 0 "")\n'
        '  (net 1 "N1")\n'
        '  (net 2 "GND")\n'
        ')\n',
        encoding="utf-8",
    )

    board = _board()
    result = route_board(board, compute_ratsnest(board))
    assert result.segments

    output = tmp_path / "routed.kicad_pcb"
    write_routed_board(source, result, output)

    text = output.read_text(encoding="utf-8")
    assert text.count("(segment ") == len(result.segments)

    parsed = parse_board(output)
    assert len(parsed.segments) == len(result.segments)
    for written, original in zip(parsed.segments, result.segments):
        assert written.x1 == pytest.approx(original.x1)
        assert written.y1 == pytest.approx(original.y1)
        assert written.x2 == pytest.approx(original.x2)
        assert written.y2 == pytest.approx(original.y2)
        assert written.width == pytest.approx(original.width)
        assert written.layer == original.layer
        assert written.net_code == original.net_code


def test_write_routed_board_no_trailing_newline(tmp_path: Path) -> None:
    source = tmp_path / "tight.kicad_pcb"
    source.write_text('(kicad_pcb (version 20221018) (net 0 "") (net 1 "N1"))', encoding="utf-8")

    result = RouteResult(
        segments=[TrackSegment(x1=1.0, y1=2.0, x2=3.0, y2=2.0, width=0.25, layer="F.Cu", net_code=1)],
        routed=[],
        failed=[],
    )
    output = tmp_path / "tight_routed.kicad_pcb"
    write_routed_board(source, result, output)

    parsed = parse_board(output)
    assert len(parsed.segments) == 1
    assert parsed.segments[0].length == pytest.approx(2.0)


def test_wall_requires_two_layer_routing() -> None:
    board = _wall_board()
    report = compute_ratsnest(board)
    assert len(report.airwires) == 1  # sanity: one N1 airwire, GND wall is one cluster
    airwire = report.airwires[0]

    # Single-layer routing cannot cross the F.Cu wall.
    single = route_board(board, report, layers=None)
    assert single.failed == [airwire]
    assert not single.routed
    assert not single.vias

    # Two-layer routing dives under the wall and comes back up.
    result = route_board(board, report, layers=("F.Cu", "B.Cu"))
    assert result.routed == [airwire]
    assert not result.failed
    assert len(result.vias) >= 2
    assert {s.layer for s in result.segments} == {"F.Cu", "B.Cu"}

    # Every via clears the wall's net-2 copper (centerline distance minus half-width).
    for via in result.vias:
        assert via.net_code == 1
        for wall in _wall_segments():
            gap = _point_segment_distance(via.x, via.y, wall) - WALL_WIDTH / 2
            assert gap > CLEARANCE

    summary = result.to_dict()
    assert summary["airwires_routed"] == 1
    assert summary["vias_added"] == len(result.vias)


def test_write_routed_board_with_vias(tmp_path: Path) -> None:
    source = tmp_path / "wall.kicad_pcb"
    source.write_text(
        '(kicad_pcb (version 20221018) (generator test)\n'
        '  (net 0 "")\n'
        '  (net 1 "N1")\n'
        '  (net 2 "GND")\n'
        '  (footprint "test:R" (at 100 100) (property "Reference" "R1")\n'
        '    (pad "1" smd rect (at 0 0) (size 0.8 0.8) (net 1 "N1")))\n'
        '  (footprint "test:R" (at 110 100) (property "Reference" "R2")\n'
        '    (pad "1" smd rect (at 0 0) (size 0.8 0.8) (net 1 "N1")))\n'
        f'  (segment (start {WALL_X} 95) (end {WALL_X} 100) (width {WALL_WIDTH}) (layer "F.Cu") (net 2))\n'
        f'  (segment (start {WALL_X} 100) (end {WALL_X} 105) (width {WALL_WIDTH}) (layer "F.Cu") (net 2))\n'
        ')\n',
        encoding="utf-8",
    )

    board = parse_board(source)
    report = compute_ratsnest(board)
    result = route_board(board, report, layers=("F.Cu", "B.Cu"))
    assert result.routed
    assert len(result.vias) >= 2

    output = tmp_path / "wall_routed.kicad_pcb"
    write_routed_board(source, result, output)

    text = output.read_text(encoding="utf-8")
    assert text.count("(via ") == len(result.vias)
    assert '(size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu")' in text

    # The written board re-parses and the parsed vias include the new ones.
    parsed = parse_board(output)
    assert len(parsed.segments) == len(board.segments) + len(result.segments)
    for via in result.vias:
        assert any(
            v.net_code == via.net_code
            and v.x == pytest.approx(via.x)
            and v.y == pytest.approx(via.y)
            for v in parsed.vias
        )

    # The new copper closes N1: vias are junctions and connectivity is layer-agnostic.
    after = compute_ratsnest(parsed)
    n1 = next(n for n in after.nets if n.net_code == 1)
    assert n1.fully_routed
    assert not n1.airwires
