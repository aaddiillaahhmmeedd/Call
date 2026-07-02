"""Tests for the design-rule checker in netlist_agent.drc."""

from __future__ import annotations

from netlist_agent.drc import DrcViolation, check_board
from netlist_agent.kicad_pcb import Board, NetClass, Pad, TrackSegment, Via

NETS = {1: "GND", 2: "VCC"}


def make_pad(x: float, y: float, net_code: int, radius: float = 0.5, ref: str = "R1", name: str = "1") -> Pad:
    return Pad(
        reference=ref,
        pad_name=name,
        x=x,
        y=y,
        net_code=net_code,
        net_name=NETS.get(net_code, ""),
        radius=radius,
    )


def make_segment(
    x1: float, y1: float, x2: float, y2: float,
    net_code: int, width: float = 0.2, layer: str = "F.Cu",
) -> TrackSegment:
    return TrackSegment(x1=x1, y1=y1, x2=x2, y2=y2, width=width, layer=layer, net_code=net_code)


def test_clean_board_returns_empty_list() -> None:
    board = Board(
        nets=dict(NETS),
        pads=[make_pad(0.0, 0.0, 1), make_pad(10.0, 0.0, 2, ref="R2")],
        segments=[
            make_segment(0.0, 0.0, 5.0, 0.0, 1),
            make_segment(10.0, 0.0, 10.0, 5.0, 2),
        ],
        vias=[Via(x=5.0, y=0.0, net_code=1)],
    )
    assert check_board(board) == []


def test_parallel_segments_of_different_nets_too_close() -> None:
    # 0.2 mm wide centerlines 0.3 mm apart -> 0.1 mm edge gap < 0.15 mm.
    board = Board(
        nets=dict(NETS),
        segments=[
            make_segment(0.0, 0.0, 10.0, 0.0, 1),
            make_segment(0.0, 0.3, 10.0, 0.3, 2),
        ],
    )
    violations = check_board(board)
    assert len(violations) == 1
    v = violations[0]
    assert v.kind == "clearance"
    assert {v.net_a, v.net_b} == {"GND", "VCC"}
    assert "0.1" in v.message
    assert v.y == 0.15  # midpoint of closest approach between the centerlines
    assert v.to_dict()["kind"] == "clearance"


def test_pad_grazing_foreign_segment() -> None:
    # Pad radius 0.5 at y=0, segment edge at y=0.6 -> 0.1 mm gap.
    board = Board(
        nets=dict(NETS),
        pads=[make_pad(0.0, 0.0, 1)],
        segments=[make_segment(-5.0, 0.7, 5.0, 0.7, 2)],
    )
    violations = check_board(board)
    assert len(violations) == 1
    assert violations[0].kind == "clearance"
    assert "R1.1" in violations[0].message


def test_overlapping_pads() -> None:
    board = Board(
        nets=dict(NETS),
        pads=[make_pad(0.0, 0.0, 1), make_pad(0.5, 0.0, 2, ref="R2")],
    )
    violations = check_board(board)
    assert len(violations) == 1
    v = violations[0]
    assert v.kind == "clearance"
    assert (v.x, v.y) == (0.25, 0.0)
    assert "-0.5" in v.message  # pads overlap: negative edge distance


def test_via_next_to_foreign_segment() -> None:
    # Via copper radius 0.4 at y=0, segment edge at y=0.5 -> 0.1 mm gap.
    board = Board(
        nets=dict(NETS),
        segments=[make_segment(-5.0, 0.6, 5.0, 0.6, 2)],
        vias=[Via(x=0.0, y=0.0, net_code=1)],
    )
    violations = check_board(board)
    assert len(violations) == 1
    assert violations[0].kind == "clearance"
    assert "via" in violations[0].message


def test_thin_segment_flags_track_width() -> None:
    board = Board(nets=dict(NETS), segments=[make_segment(0.0, 0.0, 4.0, 0.0, 1, width=0.1)])
    violations = check_board(board)
    assert len(violations) == 1
    v = violations[0]
    assert v.kind == "track_width"
    assert (v.x, v.y) == (2.0, 0.0)
    assert v.net_a == "GND"
    assert v.net_b is None


def test_zero_width_segment_is_skipped() -> None:
    board = Board(nets=dict(NETS), segments=[make_segment(0.0, 0.0, 4.0, 0.0, 1, width=0.0)])
    assert check_board(board) == []


def test_same_net_near_contact_is_fine() -> None:
    board = Board(
        nets=dict(NETS),
        pads=[make_pad(0.0, 0.0, 1), make_pad(0.5, 0.0, 1, ref="R2")],
        segments=[
            make_segment(0.0, 0.0, 10.0, 0.0, 1),
            make_segment(0.0, 0.05, 10.0, 0.05, 1),
        ],
        vias=[Via(x=0.0, y=0.1, net_code=1)],
    )
    assert check_board(board) == []


def test_different_layer_segments_do_not_violate_clearance() -> None:
    board = Board(
        nets=dict(NETS),
        segments=[
            make_segment(0.0, 0.0, 10.0, 0.0, 1, layer="F.Cu"),
            make_segment(0.0, 0.0, 10.0, 0.0, 2, layer="B.Cu"),
        ],
    )
    assert check_board(board) == []


def test_crossing_segments_reported_once() -> None:
    # Crossing centerlines: distance 0, one violation for the pair (dedup).
    board = Board(
        nets=dict(NETS),
        segments=[
            make_segment(-5.0, 0.0, 5.0, 0.0, 1),
            make_segment(0.0, -5.0, 0.0, 5.0, 2),
        ],
    )
    violations = check_board(board)
    assert len(violations) == 1
    v = violations[0]
    assert v.kind == "clearance"
    assert (v.x, v.y) == (0.0, 0.0)  # intersection point
    assert "-0.2" in v.message  # fully overlapping copper


def test_violations_sorted_by_kind_x_y() -> None:
    board = Board(
        nets=dict(NETS),
        segments=[
            make_segment(20.0, 0.0, 24.0, 0.0, 1, width=0.1),  # track_width at x=22
            make_segment(-5.0, 0.0, 5.0, 0.0, 1),
            make_segment(0.0, -5.0, 0.0, 5.0, 2),  # clearance at (0, 0)
        ],
    )
    kinds = [v.kind for v in check_board(board)]
    assert kinds == ["clearance", "track_width"]


def test_net_class_clearance_tightens_check() -> None:
    # 0.2 mm wide centerlines 0.5 mm apart -> 0.3 mm edge gap: fine at the
    # global 0.15 mm default, but VCC's "Power" class requires 0.4 mm.
    segments = [
        make_segment(0.0, 0.0, 10.0, 0.0, 1),
        make_segment(0.0, 0.5, 10.0, 0.5, 2),
    ]
    clean = Board(nets=dict(NETS), segments=list(segments))
    assert check_board(clean) == []

    board = Board(
        nets=dict(NETS),
        segments=segments,
        net_classes={"Power": NetClass(name="Power", clearance=0.4, nets=["VCC"])},
    )
    violations = check_board(board)
    assert len(violations) == 1
    v = violations[0]
    assert v.kind == "clearance"
    assert {v.net_a, v.net_b} == {"GND", "VCC"}
    assert "0.3 mm < required 0.4 mm" in v.message


def test_net_class_trace_width_tightens_check() -> None:
    # Both segments are 0.2 mm wide (>= the 0.15 mm global minimum), but the
    # VCC segment's "Power" class demands 0.5 mm.
    board = Board(
        nets=dict(NETS),
        segments=[
            make_segment(0.0, 0.0, 4.0, 0.0, 1),
            make_segment(0.0, 5.0, 4.0, 5.0, 2),
        ],
        net_classes={"Power": NetClass(name="Power", trace_width=0.5, nets=["VCC"])},
    )
    violations = check_board(board)
    assert len(violations) == 1
    v = violations[0]
    assert v.kind == "track_width"
    assert v.net_a == "VCC"
    assert "0.2 mm < minimum 0.5 mm" in v.message


def test_to_dict_round_trip_fields() -> None:
    v = DrcViolation(kind="clearance", message="m", x=1.0, y=2.0, net_a="GND", net_b="VCC")
    assert v.to_dict() == {
        "kind": "clearance",
        "message": "m",
        "x": 1.0,
        "y": 2.0,
        "net_a": "GND",
        "net_b": "VCC",
    }
