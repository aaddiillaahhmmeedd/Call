from pathlib import Path

from netlist_agent.kicad_pcb import parse_board
from netlist_agent.panel import PanelSpec, panelize
from netlist_agent.ratsnest import compute_ratsnest

# One footprint R1 (two pads), one segment, one 10x8 mm Edge.Cuts gr_rect outline.
BOARD = (
    '(kicad_pcb (version 20221018) (generator test)\n'
    '  (net 0 "")\n'
    '  (net 1 "N1")\n'
    '  (footprint "test:R" (at 5 4)\n'
    '    (property "Reference" "R1" (at 0 -1))\n'
    '    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 1 "N1"))\n'
    '    (pad "2" smd rect (at 1 0) (size 0.8 0.8) (net 1 "N1")))\n'
    '  (segment (start 4 4) (end 6 4) (width 0.25) (layer "F.Cu") (net 1))\n'
    '  (gr_rect (start 0 0) (end 10 8) (layer "Edge.Cuts") (width 0.1))\n'
    ')\n'
)


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "board.kicad_pcb"
    path.write_text(BOARD, encoding="utf-8")
    return path


def test_panelize_2x2(tmp_path: Path) -> None:
    output = tmp_path / "panel.kicad_pcb"
    stats = panelize(_source(tmp_path), PanelSpec(rows=2, cols=2, gap_mm=3.0, rail_mm=5.0), output)
    board = parse_board(output)

    # Outline is 10x8 mm, so the pitch is bbox + 3 mm gap.
    assert stats["copies"] == 4
    assert stats["pitch_mm"] == [13.0, 11.0]
    assert stats["references"] == 4
    assert stats["panel_bbox"] == [0.0, -8.0, 23.0, 27.0]  # rails included

    assert len(board.footprints) == 4
    assert {fp.reference for fp in board.footprints} == {"R1", "R1_2", "R1_3", "R1_4"}

    # Copy (0, 1) is R1_2: its pads sit exactly pitch_x = 13.0 right of R1's.
    originals = sorted((p.x, p.y) for p in board.pads if p.reference == "R1")
    shifted = sorted((p.x, p.y) for p in board.pads if p.reference == "R1_2")
    assert originals == [(4.0, 4.0), (6.0, 4.0)]
    assert shifted == [(x + 13.0, y) for x, y in originals]

    assert len(board.segments) == 4
    # 4 outline rects (16 sides) plus two rails of 4 gr_lines each.
    assert len(board.edge_segments) == 16 + 8


def test_panel_ratsnest_and_determinism(tmp_path: Path) -> None:
    source = _source(tmp_path)
    first = tmp_path / "panel_a.kicad_pcb"
    second = tmp_path / "panel_b.kicad_pcb"
    panelize(source, PanelSpec(rows=2, cols=2, gap_mm=3.0, rail_mm=5.0), first)
    panelize(source, PanelSpec(rows=2, cols=2, gap_mm=3.0, rail_mm=5.0), second)
    assert first.read_bytes() == second.read_bytes()

    report = compute_ratsnest(parse_board(first))  # copies share net codes: must not crash
    assert report.nets is not None


def test_1x1_without_rails_is_a_plain_copy(tmp_path: Path) -> None:
    output = tmp_path / "single.kicad_pcb"
    stats = panelize(_source(tmp_path), PanelSpec(rows=1, cols=1, gap_mm=3.0, rail_mm=0.0), output)

    assert stats["copies"] == 1
    assert stats["references"] == 1
    assert output.read_text(encoding="utf-8") == BOARD  # nothing added
