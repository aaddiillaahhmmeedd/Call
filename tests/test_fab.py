from pathlib import Path

import pytest

from netlist_agent.fab import readable_angle, render_assembly_svg, write_assembly_svg
from netlist_agent.kicad_pcb import Board, parse_board
from netlist_agent.placement import courtyard_extents

_BOARD = """\
(kicad_pcb (version 20221018) (generator test)
  (net 0 "")
  (net 1 "N")
  (gr_rect (start 100 100) (end 130 120) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts"))
  (footprint "test:R" (at 110 110 90)
    (property "Reference" "R<1>" (at 0 -1))
    (fp_rect (start -2 -1.5) (end 2 1.5) (layer "F.CrtYd") (stroke (width 0.05) (type solid)))
    (pad "1" smd rect (at -1 0) (size 0.8 0.8) (net 1 "N"))
    (pad "2" smd rect (at 1 0) (size 0.8 0.8) (net 1 "N")))
  (footprint "test:C" (at 120 110)
    (property "Reference" "C2" (at 0 -1))
    (pad "1" smd rect (at -0.5 0) (size 0.6 0.6) (net 1 "N"))
    (pad "2" smd rect (at 0.5 0) (size 0.6 0.6) (net 1 "N")))
)
"""


@pytest.fixture()
def board(tmp_path: Path) -> Board:
    source = tmp_path / "assembly.kicad_pcb"
    source.write_text(_BOARD, encoding="utf-8")
    return parse_board(source)


def test_svg_structure_and_outline(board: Board) -> None:
    svg = render_assembly_svg(board)

    assert svg.startswith("<svg")
    # Outline bbox (100,100)-(130,120) at scale 20 with a 2 mm margin: the
    # solid dark outline rectangle lands at (40, 40), 600 x 400 SVG units.
    assert (
        '<rect x="40.0" y="40.0" width="600.00" height="400.00" '
        'fill="none" stroke="#333333"' in svg
    )


def test_one_group_per_footprint_with_escaped_refs(board: Board) -> None:
    svg = render_assembly_svg(board)

    assert svg.count("<g data-ref=") == len(board.footprints) == 2
    assert 'data-ref="R&lt;1&gt;"' in svg
    assert 'data-ref="C2"' in svg
    assert ">R&lt;1&gt;</text>" in svg
    assert ">C2</text>" in svg
    assert "R<1>" not in svg  # never emitted unescaped
    # Deterministic natural-sort ordering: C2 before R<1>.
    assert svg.index('data-ref="C2"') < svg.index('data-ref="R&lt;1&gt;"')


def test_rotated_text_transform_and_pin1_markers(board: Board) -> None:
    svg = render_assembly_svg(board)

    # R<1> sits at (110, 110) -> SVG (240, 240); its 90-degree rotation is
    # kept readable and negated for SVG's clockwise rotate().
    assert 'transform="rotate(-90 240.0 240.0)">R&lt;1&gt;</text>' in svg
    c2_group = svg.split('data-ref="C2"')[1].split("</g>")[0]
    assert 'transform="rotate' not in c2_group  # unrotated: no transform
    assert svg.count('class="pin1"') == len(board.footprints) == 2
    # R<1>'s pad "1" is at local (-1, 0), rotated 90 -> board (110, 111) -> SVG (240, 260).
    assert '<circle class="pin1" cx="240.0" cy="260.0"' in svg


def test_courtyard_and_fallback_rect_dimensions(board: Board) -> None:
    svg = render_assembly_svg(board)
    footprints = {fp.reference: fp for fp in board.footprints}

    # R<1>: courtyard half-extents (2, 1.5) rotated 90 -> (1.5, 2), so the
    # drawn rectangle is 3 x 4 mm = 60 x 80 SVG units at scale 20.
    half_w, half_h = courtyard_extents(footprints["R<1>"])
    assert (half_w, half_h) == pytest.approx((1.5, 2.0))
    assert f'width="{2 * half_w * 20:.2f}" height="{2 * half_h * 20:.2f}"' in svg
    assert 'width="60.00" height="80.00"' in svg

    # C2 has no courtyard: fallback rectangle over its pad centers
    # (119.5..120.5, 110) grown by 0.5 mm -> 2 x 1 mm = 40 x 20 SVG units.
    assert courtyard_extents(footprints["C2"]) is None
    assert 'width="40.00" height="20.00"' in svg


def test_readable_angle_flips_upside_down_text() -> None:
    assert readable_angle(0) == 0.0
    assert readable_angle(90) == 90.0
    assert readable_angle(180) == 0.0  # flipped back upright
    assert readable_angle(270) == 90.0  # 270 is still upside down: flipped
    assert readable_angle(271) == 271.0
    assert readable_angle(-90) == 90.0  # normalizes to 270, then flips
    assert readable_angle(360) == 0.0


def test_write_assembly_svg_creates_parents(board: Board, tmp_path: Path) -> None:
    output = tmp_path / "out" / "nested" / "assembly.svg"
    write_assembly_svg(board, output)

    assert output.exists()
    written = output.read_text(encoding="utf-8")
    assert written.startswith("<svg")
    assert written == render_assembly_svg(board)
