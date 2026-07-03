import math

import pytest

from netlist_agent.impedance import (
    Stackup,
    estimate_board,
    microstrip_z0,
    stripline_z0,
    suggest_width,
)
from netlist_agent.kicad_pcb import Board, TrackSegment


def _seg(width: float, net_code: int) -> TrackSegment:
    return TrackSegment(x1=0.0, y1=0.0, x2=10.0, y2=0.0, width=width, layer="F.Cu", net_code=net_code)


def test_microstrip_golden_value() -> None:
    s = Stackup(er=4.5, height_mm=0.2, thickness_mm=0.035)
    expected = 87.0 / math.sqrt(4.5 + 1.41) * math.log(5.98 * 0.2 / (0.8 * 0.35 + 0.035))
    assert microstrip_z0(0.35, s) == pytest.approx(expected)
    assert 40.0 < expected < 60.0  # sanity: near 50 ohms for this geometry


def test_monotonic_and_stripline_lower() -> None:
    s = Stackup()
    assert microstrip_z0(0.2, s) > microstrip_z0(0.4, s)
    assert stripline_z0(0.25, s) < microstrip_z0(0.25, s)


def test_suggest_width_round_trips() -> None:
    s = Stackup()
    width = suggest_width(50.0, s)
    assert microstrip_z0(width, s) == pytest.approx(50.0, abs=0.05)
    # Clamping at the bounds (targets beyond what the width range can reach).
    assert suggest_width(1000.0, s) == pytest.approx(0.05)
    assert suggest_width(-100.0, s) == pytest.approx(5.0)


def test_estimate_board_targets() -> None:
    board = Board(
        nets={0: "", 1: "SIG", 2: "RF"},
        segments=[_seg(0.25, 1), _seg(0.5, 1), _seg(0.35, 2)],
    )
    s = Stackup()

    loose = estimate_board(board, s, target=50.0, tolerance_pct=40.0)
    assert [n.net_name for n in loose] == ["SIG", "RF"]
    sig = loose[0]
    assert sig.widths_mm == [0.25, 0.5]
    assert set(sig.z0_ohms) == {"0.25", "0.5"}
    assert sig.to_dict()["within_tolerance"] is True

    tight = estimate_board(board, s, target=50.0, tolerance_pct=1.0)
    assert tight[0].to_dict()["within_tolerance"] is False

    no_target = estimate_board(board, s)
    assert "within_tolerance" not in no_target[0].to_dict()
