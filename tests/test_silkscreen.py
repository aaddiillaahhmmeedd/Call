import math
import re
from pathlib import Path

import pytest

from netlist_agent.kicad_pcb import Board, Footprint
from netlist_agent.silkscreen import (
    STROKES,
    export_silkscreen,
    text_strokes,
    write_silkscreen,
)


def _board() -> Board:
    return Board(
        footprints=[
            Footprint(reference="R1", name="Resistor_SMD:R_0603", x=10.0, y=100.0, rotation=0.0, courtyard_half_h=0.8),
            Footprint(reference="C1", name="Capacitor_SMD:C_0603", x=30.0, y=100.0, rotation=180.0, courtyard_half_h=0.8),
            Footprint(reference="?", name="Unplaced", x=50.0, y=100.0),
        ]
    )


def test_glyph_set_covers_required_characters() -> None:
    required = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-?+.")
    assert required <= set(STROKES)
    for glyph, strokes in STROKES.items():
        assert strokes, glyph
        for x1, y1, x2, y2 in strokes:
            assert 0.0 <= x1 <= 0.6 and 0.0 <= x2 <= 0.6, glyph
            assert 0.0 <= y1 <= 1.0 and 0.0 <= y2 <= 1.0, glyph


def test_text_strokes_layout_and_centering() -> None:
    segments = text_strokes("R1", 0.0, 0.0, height=2.0)
    assert segments

    xs = [v for x1, _, x2, _ in segments for v in (x1, x2)]
    ys = [v for _, y1, _, y2 in segments for v in (y1, y2)]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)

    # Two characters at advance 0.8 * height = 1.6 mm each; the ink bbox is
    # one inter-glyph gap (0.2 * height) narrower than 2 * advance.
    advance = 0.8 * 2.0
    assert abs(width - 2 * advance) <= 0.2 * 2.0 + 1e-9
    assert height <= 2.0 + 1e-9

    # Centered on the origin.
    assert abs((max(xs) + min(xs)) / 2) < 1e-9
    assert abs((max(ys) + min(ys)) / 2) < 1e-9


def test_text_strokes_rotation_90() -> None:
    cx, cy = 5.0, 7.0
    flat = text_strokes("L", cx, cy, height=1.0, rotation=0.0)
    rotated = text_strokes("L", cx, cy, height=1.0, rotation=90.0)
    assert len(flat) == len(rotated)

    # Board convention (y-down): x' = cx + dx*cos(t) + dy*sin(t),
    # y' = cy - dx*sin(t) + dy*cos(t). At t=90: x' = cx + dy, y' = cy - dx.
    theta = math.radians(90.0)
    for (x1, y1, x2, y2), (rx1, ry1, rx2, ry2) in zip(flat, rotated):
        for (px, py), (qx, qy) in (((x1, y1), (rx1, ry1)), ((x2, y2), (rx2, ry2))):
            dx, dy = px - cx, py - cy
            assert qx == pytest.approx(cx + dx * math.cos(theta) + dy * math.sin(theta), abs=1e-9)
            assert qy == pytest.approx(cy - dx * math.sin(theta) + dy * math.cos(theta), abs=1e-9)


def test_text_strokes_unknown_chars_advance() -> None:
    # "@" has no glyph: it advances but adds no strokes, so "A@A" is wider
    # than "AA" while containing the same number of segments.
    with_gap = text_strokes("A@A", 0.0, 0.0, height=1.0)
    without = text_strokes("AA", 0.0, 0.0, height=1.0)
    assert len(with_gap) == len(without)
    span = lambda segs: max(v for x1, _, x2, _ in segs for v in (x1, x2)) - min(
        v for x1, _, x2, _ in segs for v in (x1, x2)
    )
    assert span(with_gap) > span(without)


def test_export_silkscreen_framing_and_content() -> None:
    text = export_silkscreen(_board())
    lines = text.splitlines()

    assert lines[0] == "%FSLAX46Y46*%"
    assert lines[1] == "%MOMM*%"
    assert "G04 Layer: F.SilkS*" in text
    assert "%ADD10C,0.150*%" in text
    assert lines[-1] == "M02*"

    # Deterministic natural order: C1 before R1; the "?" footprint is skipped.
    assert 0 < text.index("G04 C1*") < text.index("G04 R1*")
    assert text.count("G04 ") == 3  # layer comment + two footprints

    # At least one draw per footprint.
    c1_block = text[text.index("G04 C1*") : text.index("G04 R1*")]
    r1_block = text[text.index("G04 R1*") :]
    assert "D01*" in c1_block
    assert "D01*" in r1_block


def test_export_silkscreen_normalizes_upside_down_rotation() -> None:
    flipped = Board(footprints=[Footprint(reference="C1", name="C", x=30.0, y=100.0, rotation=180.0)])
    upright = Board(footprints=[Footprint(reference="C1", name="C", x=30.0, y=100.0, rotation=0.0)])
    assert export_silkscreen(flipped) == export_silkscreen(upright)

    # A 90-degree rotation is readable and must be preserved.
    quarter = Board(footprints=[Footprint(reference="C1", name="C", x=30.0, y=100.0, rotation=90.0)])
    assert export_silkscreen(quarter) != export_silkscreen(upright)


def test_export_silkscreen_negates_y() -> None:
    text = export_silkscreen(_board())
    y_values = [int(match) for match in re.findall(r"Y(-?\d+)D0[12]\*", text)]
    assert y_values
    # Footprints sit at board y=100 (y-down), so every Gerber Y is negative,
    # and the text sits above the footprint: |Y| < 100 mm in 4.6 units.
    assert all(value < 0 for value in y_values)
    assert all(abs(value) < 100_000_000 for value in y_values)


def test_export_silkscreen_text_above_courtyard() -> None:
    board = Board(
        footprints=[Footprint(reference="R1", name="R", x=10.0, y=100.0, rotation=0.0, courtyard_half_h=2.0)]
    )
    text = export_silkscreen(board, text_height=1.0)
    y_mm = [-int(match) / 1e6 for match in re.findall(r"Y(-?\d+)D0[12]\*", text)]
    # Text is centered on y - 2.0 - 1.0 = 97.0, so all strokes lie in [96.5, 97.5].
    assert all(96.5 - 1e-6 <= y <= 97.5 + 1e-6 for y in y_mm)


def test_write_silkscreen_round_trip(tmp_path: Path) -> None:
    board = _board()
    output = tmp_path / "gerbers" / "nested" / "board-F_SilkS.gbr"
    write_silkscreen(board, output)
    assert output.read_text(encoding="utf-8") == export_silkscreen(board)
