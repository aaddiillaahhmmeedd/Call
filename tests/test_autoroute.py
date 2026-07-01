import math
from pathlib import Path

import pytest

from netlist_agent.autoroute import RouteResult, route_board, write_routed_board
from netlist_agent.kicad_pcb import Board, Pad, TrackSegment, parse_board
from netlist_agent.ratsnest import compute_ratsnest

OBSTACLE = (105.0, 100.0)
OBSTACLE_RADIUS = 1.0


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
