import time

from netlist_agent.drc import check_board
from netlist_agent.kicad_pcb import Board, Pad, TrackSegment, Zone
from netlist_agent.ratsnest import compute_ratsnest


def _grid_board(n: int = 400, squeeze: bool = False) -> Board:
    """n two-pad components on a 5 mm grid, one net per component, each closed."""
    board = Board(nets={0: ""})
    for i in range(n):
        net = i + 1
        board.nets.setdefault(net, f"N{net}")
        cx, cy = 5.0 * (i % 20), 5.0 * (i // 20)
        board.pads.append(Pad(f"R{i}", "1", cx - 1.0, cy, net, f"N{net}", 0.4))
        board.pads.append(Pad(f"R{i}", "2", cx + 1.0, cy, net, f"N{net}", 0.4))
        board.segments.append(
            TrackSegment(cx - 1.0, cy, cx + 1.0, cy, 0.25, "F.Cu", net)
        )
    board.zones.append(
        Zone(net_code=1, layer="B.Cu", polygons=[[(-2.0, -2.0), (100.0, -2.0), (100.0, 100.0), (-2.0, 100.0)]])
    )
    if squeeze:  # plant a violation: a foreign pad 0.05 mm from R0's pad edge
        board.nets[999] = "SQUEEZED"
        board.pads.append(Pad("X1", "1", -1.85, 0.0, 999, "SQUEEZED", 0.4))
    return board


def test_ratsnest_scales() -> None:
    board = _grid_board()
    start = time.monotonic()
    report = compute_ratsnest(board)
    elapsed = time.monotonic() - start

    assert elapsed < 3.0, f"ratsnest took {elapsed:.2f}s on 800 pads"
    assert len(report.nets) == 400
    assert all(n.fully_routed for n in report.nets)


def test_drc_scales_and_stays_exact() -> None:
    clean = _grid_board()
    start = time.monotonic()
    violations = check_board(clean)
    elapsed = time.monotonic() - start

    assert elapsed < 3.0, f"DRC took {elapsed:.2f}s on 800 pads + 400 segments"
    assert violations == []

    squeezed = _grid_board(squeeze=True)
    found = check_board(squeezed)
    assert len(found) >= 1
    assert any("X1.1" in v.message for v in found)


def test_spatial_prefilter_matches_brute_force() -> None:
    """The grid index must be a pure accelerator: same results as O(n^2)."""
    from netlist_agent import drc

    board = _grid_board(n=40, squeeze=True)
    board.vias.append(drc.Via(x=-1.3, y=0.5, net_code=999))

    fast = [v.to_dict() for v in check_board(board)]

    # Brute force: force every pair through the same math by making one giant
    # grid cell (patch the prefilter to return all pairs).
    real = drc._candidate_pairs
    try:
        def everything(b, _clear):
            items = (
                [("s", i) for i in range(len(b.segments))]
                + [("p", i) for i in range(len(b.pads))]
                + [("v", i) for i in range(len(b.vias))]
            )
            return {
                (a, c) if a <= c else (c, a)
                for i, a in enumerate(items)
                for c in items[i + 1 :]
            }

        drc._candidate_pairs = everything
        brute = [v.to_dict() for v in check_board(board)]
    finally:
        drc._candidate_pairs = real

    assert fast == brute
