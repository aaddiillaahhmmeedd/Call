from pathlib import Path

import pytest

from netlist_agent.kicad_pcb import Board, Pad, outline_bbox, parse_board
from netlist_agent.placement import PlacementResult, optimize_placement, write_placed_board


def _pad(ref: str, name: str, x: float, y: float, net_code: int, net_name: str) -> Pad:
    return Pad(reference=ref, pad_name=name, x=x, y=y, net_code=net_code, net_name=net_name, radius=0.4)


def _bad_board() -> Board:
    """Four two-pad components in a row, but connected far-near-far."""
    pads: list[Pad] = []
    nets = [(1, "N1"), (2, "N2"), (2, "N2"), (1, "N1")]
    for i, (net_code, net_name) in enumerate(nets):
        cx = 100.0 + 10.0 * i
        ref = f"R{i + 1}"
        pads.append(_pad(ref, "1", cx - 1.0, 100.0, 0, ""))
        pads.append(_pad(ref, "2", cx + 1.0, 100.0, net_code, net_name))
    return Board(nets={0: "", 1: "N1", 2: "N2"}, pads=pads)


def test_optimize_improves_and_is_deterministic() -> None:
    result = optimize_placement(_bad_board(), iterations=2000, seed=1)

    assert result.final_length < result.initial_length
    assert result.to_dict()["improvement_pct"] > 20.0
    assert result.iterations == 2000
    assert set(result.positions) == {"R1", "R2", "R3", "R4"}

    again = optimize_placement(_bad_board(), iterations=2000, seed=1)
    assert again.positions == result.positions
    assert again.final_length == pytest.approx(result.final_length)


def test_no_overlap_after_placement() -> None:
    result = optimize_placement(_bad_board(), iterations=2000, seed=1, spacing=0.5)

    # Courtyard half-extents per component: pad reach per axis (1.0, 0.0) + pad radius (0.4) + spacing.
    half_w = 1.0 + 0.4 + 0.5
    half_h = 0.0 + 0.4 + 0.5
    refs = sorted(result.positions)
    for i, a in enumerate(refs):
        for b in refs[i + 1 :]:
            (xa, ya), (xb, yb) = result.positions[a], result.positions[b]
            separated = abs(xb - xa) >= 2 * half_w - 0.2 or abs(yb - ya) >= 2 * half_h - 0.2
            assert separated, f"{a} and {b} overlap: dx={abs(xb - xa):.2f} dy={abs(yb - ya):.2f}"


def test_fixed_components_stay_put() -> None:
    result = optimize_placement(_bad_board(), iterations=2000, seed=1, fixed={"R1", "R4"})

    assert result.positions["R1"] == (100.0, 100.0)
    assert result.positions["R4"] == (130.0, 100.0)
    assert result.final_length < result.initial_length  # R2/R3 still improve


def test_unmovable_and_empty_board() -> None:
    board = Board(nets={0: ""}, pads=[_pad("R1", "1", 100.0, 100.0, 0, "")])
    result = optimize_placement(board, iterations=100, seed=0)
    assert result.initial_length == result.final_length == 0.0
    assert result.positions["R1"] == (100.0, 100.0)


def _resistor(ref: str, x: float, y: float) -> str:
    return (
        f'  (footprint "test:R" (at {x} {y})\n'
        f'    (property "Reference" "{ref}" (at 0 -1))\n'
        f'    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 1 "N"))\n'
        f'    (pad "2" smd rect (at 1 0) (size 0.8 0.8) (net 1 "N")))\n'
    )


def test_outline_confines_components(tmp_path: Path) -> None:
    source = tmp_path / "outlined.kicad_pcb"
    source.write_text(
        '(kicad_pcb (version 20221018) (generator test)\n'
        '  (net 0 "")\n'
        '  (net 1 "N")\n'
        '  (gr_rect (start 100 100) (end 120 112) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts"))\n'
        + _resistor("R1", 95.0, 105.0)  # starts outside the outline, left
        + _resistor("R2", 110.0, 106.0)
        + _resistor("R3", 125.0, 108.0)  # starts outside the outline, right
        + ')\n',
        encoding="utf-8",
    )
    board = parse_board(source)
    bbox = outline_bbox(board)
    assert bbox == (100.0, 100.0, 120.0, 112.0)

    spacing = 0.5
    result = optimize_placement(board, iterations=3000, seed=2, spacing=spacing)

    half_w = 1.0 + 0.4 + spacing  # pad reach + pad radius + spacing
    half_h = 0.0 + 0.4 + spacing
    for ref, (x, y) in result.positions.items():
        assert x - half_w >= bbox[0] - 0.2, f"{ref} pokes out left: x={x:.2f}"
        assert x + half_w <= bbox[2] + 0.2, f"{ref} pokes out right: x={x:.2f}"
        assert y - half_h >= bbox[1] - 0.2, f"{ref} pokes out top: y={y:.2f}"
        assert y + half_h <= bbox[3] + 0.2, f"{ref} pokes out bottom: y={y:.2f}"


def test_edge_cuts_parsing_and_outline_bbox(tmp_path: Path) -> None:
    source = tmp_path / "edges.kicad_pcb"
    source.write_text(
        '(kicad_pcb (version 20221018) (generator test)\n'
        '  (net 0 "")\n'
        '  (gr_line (start 0 0) (end 30 0) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts"))\n'
        '  (gr_line (start 5 5) (end 6 6) (layer "F.SilkS"))\n'
        '  (gr_rect (start 10 -2) (end 25 20) (layer "Edge.Cuts") (stroke (width 0.1) (type solid)))\n'
        '  (gr_rect (start 1 1) (end 2 2) (layer "F.Fab"))\n'
        ')\n',
        encoding="utf-8",
    )
    board = parse_board(source)

    assert len(board.edge_segments) == 5  # one line + four rect sides; other layers ignored
    assert all(seg.layer == "Edge.Cuts" and seg.net_code == 0 for seg in board.edge_segments)
    line = board.edge_segments[0]
    assert (line.x1, line.y1, line.x2, line.y2) == (0.0, 0.0, 30.0, 0.0)
    corners = {(seg.x1, seg.y1) for seg in board.edge_segments[1:]}
    assert corners == {(10.0, -2.0), (25.0, -2.0), (25.0, 20.0), (10.0, 20.0)}

    assert outline_bbox(board) == (0.0, -2.0, 30.0, 20.0)
    assert outline_bbox(Board()) is None


def test_write_placed_board(tmp_path: Path) -> None:
    source = tmp_path / "mini.kicad_pcb"
    source.write_text(
        '(kicad_pcb (version 20221018) (generator test)\n'
        '  (net 0 "")\n'
        '  (net 1 "N")\n'
        '  (footprint "test:R" (at 100 100)\n'
        '    (property "Reference" "R1" (at 0 -1))\n'
        '    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 1 "N"))\n'
        '    (pad "2" smd rect (at 1 0) (size 0.8 0.8) (net 1 "N")))\n'
        '  (footprint "test:C" (at 110 100 90)\n'
        '    (fp_text reference "C1" (at 0 -1))\n'
        '    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 1 "N")))\n'
        ')\n',
        encoding="utf-8",
    )
    before = parse_board(source)
    r1_centroid = (100.0, 100.0)  # symmetric pads
    c1_pad = next(p for p in before.pads if p.reference == "C1")

    result = PlacementResult(
        positions={
            "R1": (r1_centroid[0] + 2.0, r1_centroid[1] + 3.0),
            "C1": (c1_pad.x + 5.0, c1_pad.y),  # single pad: centroid is the pad
        },
        initial_length=0.0,
        final_length=0.0,
        iterations=0,
    )
    output = tmp_path / "placed.kicad_pcb"
    write_placed_board(source, result, output)

    text = output.read_text(encoding="utf-8")
    assert "(at 102 103)" in text
    assert "(at 115 100 90)" in text  # rotation preserved
    assert '(property "Reference" "R1" (at 0 -1))' in text  # nested (at ...) untouched

    placed = parse_board(output)
    for pad in placed.pads:
        original = next(
            p for p in before.pads if (p.reference, p.pad_name) == (pad.reference, pad.pad_name)
        )
        dx = 2.0 if pad.reference == "R1" else 5.0
        dy = 3.0 if pad.reference == "R1" else 0.0
        assert pad.x == pytest.approx(original.x + dx)
        assert pad.y == pytest.approx(original.y + dy)
