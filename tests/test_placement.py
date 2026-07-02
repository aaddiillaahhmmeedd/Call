from pathlib import Path

import pytest

from netlist_agent.kicad_pcb import Board, Pad, parse_board
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

    # Bounding circle per component: pad reach (1.0) + pad radius (0.4) + spacing.
    radius = 1.0 + 0.4 + 0.5
    refs = sorted(result.positions)
    for i, a in enumerate(refs):
        for b in refs[i + 1 :]:
            (xa, ya), (xb, yb) = result.positions[a], result.positions[b]
            distance = ((xb - xa) ** 2 + (yb - ya) ** 2) ** 0.5
            assert distance > 2 * radius - 0.2, f"{a} and {b} overlap: {distance:.2f} mm apart"


def test_unmovable_and_empty_board() -> None:
    board = Board(nets={0: ""}, pads=[_pad("R1", "1", 100.0, 100.0, 0, "")])
    result = optimize_placement(board, iterations=100, seed=0)
    assert result.initial_length == result.final_length == 0.0
    assert result.positions["R1"] == (100.0, 100.0)


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
