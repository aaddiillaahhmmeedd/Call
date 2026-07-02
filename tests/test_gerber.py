from pathlib import Path

from netlist_agent.gerber import export_drill, export_gerbers, export_layer
from netlist_agent.kicad_pcb import Board, Pad, TrackSegment, Via, Zone


def _board() -> Board:
    return Board(
        nets={0: "", 1: "N1", 2: "GND"},
        pads=[
            Pad(reference="R1", pad_name="1", x=100.0, y=100.0, net_code=1, net_name="N1", radius=0.4),
            Pad(reference="R2", pad_name="1", x=110.0, y=100.0, net_code=1, net_name="N1", radius=0.6),
        ],
        segments=[
            TrackSegment(x1=100.0, y1=100.0, x2=105.0, y2=100.0, width=0.25, layer="F.Cu", net_code=1),
            TrackSegment(x1=105.0, y1=100.0, x2=110.0, y2=100.0, width=0.5, layer="F.Cu", net_code=1),
            TrackSegment(x1=100.0, y1=102.0, x2=110.0, y2=102.0, width=0.25, layer="B.Cu", net_code=2),
        ],
        vias=[Via(x=105.0, y=100.0, net_code=1)],
        zones=[Zone(net_code=2, layer="F.Cu", polygons=[[(98.0, 98.0), (112.0, 98.0), (112.0, 104.0), (98.0, 104.0)]])],
    )


def test_layer_framing_and_apertures() -> None:
    text = export_layer(_board(), "F.Cu")
    lines = text.splitlines()

    assert lines[0] == "%FSLAX46Y46*%"
    assert lines[1] == "%MOMM*%"
    assert lines[-1] == "M02*"

    # Deterministic aperture numbering: 2 track widths, 2 pad diameters, 1 via.
    assert "%ADD10C,0.250*%" in text
    assert "%ADD11C,0.500*%" in text
    assert "%ADD12C,0.800*%" in text  # pad d=0.8
    assert "%ADD13C,1.200*%" in text  # pad d=1.2
    assert "%ADD14C,0.800*%" in text  # via aperture


def test_layer_filtering() -> None:
    f_cu = export_layer(_board(), "F.Cu")
    b_cu = export_layer(_board(), "B.Cu")

    # The B.Cu segment (y=102) draws only in the B.Cu file.
    assert f"X{100_000_000}Y{-102_000_000}D02*" not in f_cu
    assert f"X{100_000_000}Y{-102_000_000}D02*" in b_cu
    # The zone region renders only on F.Cu.
    assert "G36*" in f_cu and "G37*" in f_cu
    assert "G36*" not in b_cu


def test_coordinates_and_flashes() -> None:
    text = export_layer(_board(), "F.Cu")

    # 4.6 format: mm * 1e6, y negated (board y-down -> Gerber y-up).
    assert "X100000000Y-100000000D02*" in text
    assert "X105000000Y-100000000D01*" in text
    # 2 pad flashes + 1 via flash.
    assert text.count("D03*") == 3

    # Region outline closes back to its first vertex.
    region = text[text.index("G36*") : text.index("G37*")]
    assert region.count("D01*") == 4  # 3 remaining vertices + closing edge
    assert region.count("D02*") == 1


def test_drill_export() -> None:
    text = export_drill(_board())
    lines = text.splitlines()

    assert lines[0] == "M48"
    assert "METRIC,TZ" in lines
    assert "T1C0.400" in lines
    assert "X105.000Y-100.000" in lines
    assert lines[-1] == "M30"


def test_drill_export_no_vias() -> None:
    text = export_drill(Board(nets={0: ""}))
    assert "T1" not in text
    assert text.splitlines()[-1] == "M30"


def test_export_gerbers_writes_files(tmp_path: Path) -> None:
    written = export_gerbers(_board(), tmp_path / "gerbers")

    names = [p.name for p in written]
    assert names == sorted(names)
    assert set(names) == {"board-F_Cu.gbr", "board-B_Cu.gbr", "board-drill.drl"}
    for path in written:
        assert path.read_text(encoding="utf-8").strip()
