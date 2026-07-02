import pytest

from netlist_agent.kicad_pcb import Board, Pad, TrackSegment
from netlist_agent.length_match import check_pairs, find_diff_pairs, net_lengths


def _pad(reference: str, x: float, y: float, net_code: int, net_name: str) -> Pad:
    return Pad(
        reference=reference,
        pad_name="1",
        x=x,
        y=y,
        net_code=net_code,
        net_name=net_name,
        radius=0.4,
    )


def _seg(x1: float, y1: float, x2: float, y2: float, net_code: int) -> TrackSegment:
    return TrackSegment(x1=x1, y1=y1, x2=x2, y2=y2, width=0.25, layer="F.Cu", net_code=net_code)


# Three nets:
#   SIG   fully routed by two chained segments (4 + 6 = 10 mm).
#   SPLIT two pads, no track -> one 3-4-5 airwire (5 mm).
#   PART  partially routed: 6 mm of track, 4 mm airwire to the far pad.
def _length_board() -> Board:
    return Board(
        nets={0: "", 1: "SIG", 2: "SPLIT", 3: "PART"},
        pads=[
            _pad("R1", 0, 0, 1, "SIG"),
            _pad("R2", 10, 0, 1, "SIG"),
            _pad("U1", 0, 10, 2, "SPLIT"),
            _pad("U2", 3, 14, 2, "SPLIT"),
            _pad("J1", 0, 20, 3, "PART"),
            _pad("J2", 10, 20, 3, "PART"),
        ],
        segments=[
            _seg(0, 0, 4, 0, 1),
            _seg(4, 0, 10, 0, 1),
            _seg(0, 20, 6, 20, 3),
        ],
    )


def test_net_lengths_sums_segments_and_airwires() -> None:
    lengths = {n.net_name: n for n in net_lengths(_length_board())}

    sig = lengths["SIG"]
    assert sig.routed_length == pytest.approx(10.0)
    assert sig.unrouted_length == pytest.approx(0.0)

    split = lengths["SPLIT"]
    assert split.routed_length == pytest.approx(0.0)
    assert split.unrouted_length == pytest.approx(5.0)
    assert split.to_dict() == {
        "net": "SPLIT",
        "routed_mm": 0.0,
        "unrouted_mm": 5.0,
        "total_mm": 5.0,
    }

    part = lengths["PART"]
    assert part.routed_length == pytest.approx(6.0)
    assert part.unrouted_length == pytest.approx(4.0)
    assert part.to_dict()["total_mm"] == pytest.approx(10.0)


def test_net_lengths_report_order() -> None:
    reported = [n.net_code for n in net_lengths(_length_board())]
    assert reported == sorted(reported)  # ratsnest report order: by net code


def test_find_diff_pairs_suffix_rules() -> None:
    board = Board(
        nets={
            0: "",
            1: "USB_P",
            2: "USB_N",
            3: "CLK+",
            4: "CLK-",
            5: "rx_p",  # case-insensitive suffix match across the pair
            6: "RX_N",
            7: "/DATA_P",  # leading "/" stripped before matching and reporting
            8: "/DATA_N",
            9: "ONLY_P",  # lone _P without a _N partner
            10: "P",  # nothing but the suffix: never pairs
            11: "EN_H",
            12: "EN_L",
            13: "USB_D+",  # D+/D- style via the +/- rule
            14: "USB_D-",
        }
    )
    assert find_diff_pairs(board) == [
        ("CLK", "CLK+", "CLK-"),
        ("DATA", "DATA_P", "DATA_N"),
        ("EN", "EN_H", "EN_L"),
        ("USB", "USB_P", "USB_N"),
        ("USB_D", "USB_D+", "USB_D-"),
        ("rx", "rx_p", "RX_N"),
    ]


def test_find_diff_pairs_ignores_unpaired_and_bare_suffix() -> None:
    board = Board(nets={0: "", 1: "ONLY_P", 2: "P", 3: "VIN"})
    assert find_diff_pairs(board) == []


def _pair_board() -> Board:
    return Board(
        nets={0: "", 1: "USB_P", 2: "USB_N", 3: "CLK+", 4: "CLK-"},
        segments=[
            _seg(0, 0, 10, 0, 1),  # USB_P: 10.0 mm
            _seg(0, 1, 10.3, 1, 2),  # USB_N: 10.3 mm -> skew 0.3, within tolerance
            _seg(0, 2, 5, 2, 3),  # CLK+: 5.0 mm
            _seg(0, 3, 7, 3, 4),  # CLK-: 7.0 mm -> skew 2.0, beyond tolerance
        ],
    )


def test_check_pairs_matched_and_mismatched() -> None:
    reports = check_pairs(_pair_board(), tolerance=0.5)
    assert [r.base_name for r in reports] == ["CLK", "USB"]

    clk, usb = reports
    assert usb.pos_length == pytest.approx(10.0)
    assert usb.neg_length == pytest.approx(10.3)
    assert usb.skew == pytest.approx(0.3)
    assert usb.to_dict()["matched"] is True

    assert clk.pos_length == pytest.approx(5.0)
    assert clk.neg_length == pytest.approx(7.0)
    assert clk.skew == pytest.approx(2.0)
    assert clk.tolerance == 0.5
    assert clk.to_dict() == {
        "pair": "CLK",
        "pos_net": "CLK+",
        "neg_net": "CLK-",
        "pos_mm": 5.0,
        "neg_mm": 7.0,
        "skew_mm": 2.0,
        "tolerance_mm": 0.5,
        "matched": False,
    }


def test_ordering_is_deterministic() -> None:
    board = _pair_board()
    assert find_diff_pairs(board) == find_diff_pairs(board)
    first = [r.to_dict() for r in check_pairs(board)]
    second = [r.to_dict() for r in check_pairs(board)]
    assert first == second
    assert [p[0] for p in find_diff_pairs(board)] == sorted(p[0] for p in find_diff_pairs(board))
