from netlist_agent.audit import audit_board
from netlist_agent.kicad_pcb import Board, Pad, TrackSegment, Via, Zone


def _pad(ref: str, x: float, y: float, net_code: int, net_name: str) -> Pad:
    return Pad(reference=ref, pad_name="1", x=x, y=y, net_code=net_code, net_name=net_name, radius=0.4)


def _seg(x1: float, y1: float, x2: float, y2: float, net_code: int, layer: str = "F.Cu") -> TrackSegment:
    return TrackSegment(x1=x1, y1=y1, x2=x2, y2=y2, width=0.25, layer=layer, net_code=net_code)


def test_clean_board_no_findings() -> None:
    board = Board(
        nets={0: "", 1: "N1"},
        pads=[_pad("R1", 100.0, 100.0, 1, "N1"), _pad("R2", 110.0, 100.0, 1, "N1")],
        segments=[_seg(100.0, 100.0, 110.0, 100.0, 1)],
    )
    assert audit_board(board) == []


def test_single_pad_net() -> None:
    board = Board(nets={0: "", 1: "LONELY"}, pads=[_pad("R1", 100.0, 100.0, 1, "LONELY")])
    findings = audit_board(board)
    assert [f.kind for f in findings] == ["single_pad_net"]
    assert findings[0].net == "LONELY"
    assert findings[0].reference == "R1"


def test_unconnected_and_missing_reference_summaries() -> None:
    board = Board(
        nets={0: ""},
        pads=[_pad("R1", 100.0, 100.0, 0, ""), _pad("?", 105.0, 100.0, 0, "")],
    )
    findings = audit_board(board)
    kinds = [f.kind for f in findings]
    assert kinds == ["missing_reference", "unconnected_pad"]
    assert "2 pad(s) have no net assigned" in findings[1].message


def test_antenna_track() -> None:
    # One end lands in R1's pad, the other dangles 2 mm from anything.
    board = Board(
        nets={0: "", 1: "N1"},
        pads=[_pad("R1", 100.0, 100.0, 1, "N1"), _pad("R2", 110.0, 100.0, 1, "N1")],
        segments=[_seg(100.0, 100.0, 108.0, 96.0, 1)],
    )
    findings = [f for f in audit_board(board) if f.kind == "antenna_track"]
    assert len(findings) == 1
    assert (findings[0].x, findings[0].y) == (108.0, 96.0)


def test_chained_segments_not_antennas() -> None:
    board = Board(
        nets={0: "", 1: "N1"},
        pads=[_pad("R1", 100.0, 100.0, 1, "N1"), _pad("R2", 110.0, 100.0, 1, "N1")],
        segments=[_seg(100.0, 100.0, 105.0, 100.0, 1), _seg(105.0, 100.0, 110.0, 100.0, 1)],
    )
    assert not [f for f in audit_board(board) if f.kind == "antenna_track"]


def test_zone_connected_endpoint_not_antenna() -> None:
    board = Board(
        nets={0: "", 1: "N1"},
        pads=[_pad("R1", 100.0, 100.0, 1, "N1")],
        segments=[_seg(100.0, 100.0, 105.0, 105.0, 1)],
        zones=[Zone(net_code=1, layer="F.Cu", polygons=[[(104.0, 104.0), (106.0, 104.0), (106.0, 106.0), (104.0, 106.0)]])],
    )
    assert not [f for f in audit_board(board) if f.kind == "antenna_track"]


def test_via_connected_endpoint_not_antenna() -> None:
    board = Board(
        nets={0: "", 1: "N1"},
        pads=[_pad("R1", 100.0, 100.0, 1, "N1")],
        segments=[_seg(100.0, 100.0, 105.0, 100.0, 1)],
        vias=[Via(x=105.0, y=100.0, net_code=1)],
    )
    assert not [f for f in audit_board(board) if f.kind == "antenna_track"]


def test_duplicate_and_zero_length_segments() -> None:
    board = Board(
        nets={0: "", 1: "N1"},
        pads=[_pad("R1", 100.0, 100.0, 1, "N1"), _pad("R2", 110.0, 100.0, 1, "N1")],
        segments=[
            _seg(100.0, 100.0, 110.0, 100.0, 1),
            _seg(110.0, 100.0, 100.0, 100.0, 1),  # same span, reversed -> duplicate
            _seg(100.0, 100.0, 100.0, 100.0, 1),  # zero length
        ],
    )
    kinds = [f.kind for f in audit_board(board)]
    assert kinds.count("duplicate_segment") == 1
    assert kinds.count("zero_length_segment") == 1
    # Different layer with same span is NOT a duplicate.
    board.segments.append(_seg(100.0, 100.0, 110.0, 100.0, 1, layer="B.Cu"))
    kinds = [f.kind for f in audit_board(board)]
    assert kinds.count("duplicate_segment") == 1
