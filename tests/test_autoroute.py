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


def _box_walls() -> list[TrackSegment]:
    # A closed net-3 box; the only routable space is its interior.
    corners = [(96.0, 92.0), (114.0, 92.0), (114.0, 108.0), (96.0, 108.0)]
    return [
        TrackSegment(x1=a[0], y1=a[1], x2=b[0], y2=b[1], width=0.4, layer="F.Cu", net_code=3)
        for a, b in zip(corners, corners[1:] + corners[:1])
    ]


def _congested_board() -> Board:
    # Net 1 spans the box horizontally with pads sealed against the side walls,
    # so its straight route splits the interior into top/bottom chambers.
    # Net 2 spans those chambers vertically: it can only route if net 1 detours
    # around net 2's copper instead of cutting straight across.
    return Board(
        nets={0: "", 1: "N1", 2: "N2", 3: "FRAME"},
        pads=[
            _pad("R1", 97.0, 100.0, 1, "N1"),
            _pad("R2", 113.0, 100.0, 1, "N1"),
            _pad("C1", 105.0, 97.0, 2, "N2"),
            _pad("C2", 105.0, 103.0, 2, "N2"),
        ],
        segments=_box_walls(),
    )


def test_rip_up_and_reroute() -> None:
    board = _congested_board()
    report = compute_ratsnest(board)
    assert len(report.airwires) == 2  # one per pad net; the frame is one closed cluster

    # Greedy order: net 1 routes straight through and net 2 is walled in.
    greedy = route_board(board, report, rip_up_retries=0)
    assert len(greedy.routed) == 1
    assert greedy.routed[0].net_code == 1
    assert len(greedy.failed) == 1
    assert greedy.failed[0].net_code == 2
    assert {s.net_code for s in greedy.segments} == {1}

    # Rip-up tears out net 1, routes net 2 straight, reroutes net 1 around it.
    result = route_board(board, report, rip_up_retries=2)
    assert not result.failed
    assert sorted(a.net_code for a in result.routed) == [1, 2]
    assert {s.net_code for s in result.segments} == {1, 2}

    # RouteResult consistency: exactly the routed airwires' copper, nothing orphaned.
    routed_board = _congested_board()
    routed_board.segments += result.segments
    after = compute_ratsnest(routed_board)
    for code in (1, 2):
        net = next(n for n in after.nets if n.net_code == code)
        assert net.fully_routed, f"net {code} not closed after rip-up routing"
    summary = result.to_dict()
    assert summary["airwires_routed"] == 2
    assert summary["airwires_failed"] == 0


def test_net_widths() -> None:
    board = Board(
        nets={0: "", 1: "N1", 2: "N2"},
        pads=[
            _pad("R1", 100.0, 100.0, 1, "N1"),
            _pad("R2", 110.0, 100.0, 1, "N1"),
            _pad("C1", 105.0, 97.0, 2, "N2"),
            _pad("C2", 105.0, 103.0, 2, "N2"),
        ],
    )
    report = compute_ratsnest(board)
    result = route_board(board, report, net_widths={1: 0.5})
    assert not result.failed

    n1_segments = [s for s in result.segments if s.net_code == 1]
    n2_segments = [s for s in result.segments if s.net_code == 2]
    assert n1_segments and n2_segments
    assert all(s.width == pytest.approx(0.5) for s in n1_segments)
    assert all(s.width == pytest.approx(0.25) for s in n2_segments)

    # Net 2 keeps extra distance from net 1's wider copper: required centerline
    # gap is 0.5/2 + clearance + 0.25/2 = 0.575, minus up to half a grid
    # diagonal of quantization between path vertices.
    for n2 in n2_segments:
        for n1 in n1_segments:
            for px, py in ((n2.x1, n2.y1), (n2.x2, n2.y2)):
                assert _point_segment_distance(px, py, n1) > 0.45

    routed_board = Board(nets=dict(board.nets), pads=list(board.pads), segments=result.segments)
    after = compute_ratsnest(routed_board)
    assert all(n.fully_routed for n in after.nets)
