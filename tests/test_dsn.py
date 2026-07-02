from pathlib import Path

from netlist_agent.dsn import export_dsn, write_dsn
from netlist_agent.kicad_pcb import Board, Footprint, NetClass, Pad, TrackSegment, Via


def _board() -> Board:
    # U1 is rotated 90 degrees; its pads' world positions follow the parser's
    # forward transform: x = fp_x + px*cos + py*sin, y = fp_y - px*sin + py*cos.
    # Pad 1 local (1.0, 0.5) -> world (120.5, 104.0); pad 2 local (-1.0, 0.5)
    # -> world (120.5, 106.0).
    return Board(
        nets={0: "", 1: "GND", 2: "NET 2"},
        pads=[
            Pad(reference="R1", pad_name="1", x=99.25, y=100.0, net_code=1, net_name="GND", radius=0.4),
            Pad(reference="R1", pad_name="2", x=100.75, y=100.0, net_code=2, net_name="NET 2", radius=0.4),
            Pad(reference="R2", pad_name="1", x=109.25, y=100.0, net_code=1, net_name="GND", radius=0.4),
            Pad(reference="R2", pad_name="2", x=110.75, y=100.0, net_code=2, net_name="NET 2", radius=0.4),
            Pad(reference="U1", pad_name="1", x=120.5, y=104.0, net_code=1, net_name="GND", radius=0.4),
            Pad(reference="U1", pad_name="2", x=120.5, y=106.0, net_code=2, net_name="NET 2", radius=0.4),
        ],
        segments=[
            TrackSegment(x1=99.25, y1=100.0, x2=109.25, y2=100.0, width=0.25, layer="F.Cu", net_code=1),
        ],
        vias=[Via(x=105.0, y=100.0, net_code=1)],
        net_classes={"Default": NetClass(name="Default", clearance=0.25, trace_width=0.3)},
        edge_segments=[
            TrackSegment(x1=95.0, y1=95.0, x2=125.0, y2=95.0, width=0.0, layer="Edge.Cuts", net_code=0),
            TrackSegment(x1=125.0, y1=95.0, x2=125.0, y2=110.0, width=0.0, layer="Edge.Cuts", net_code=0),
            TrackSegment(x1=125.0, y1=110.0, x2=95.0, y2=110.0, width=0.0, layer="Edge.Cuts", net_code=0),
            TrackSegment(x1=95.0, y1=110.0, x2=95.0, y2=95.0, width=0.0, layer="Edge.Cuts", net_code=0),
        ],
        footprints=[
            Footprint(reference="R1", name="R_0603", x=100.0, y=100.0, rotation=0.0),
            Footprint(reference="R2", name="R_0603", x=110.0, y=100.0, rotation=0.0),
            Footprint(reference="U1", name="SOIC-8", x=120.0, y=105.0, rotation=90.0),
        ],
    )


def test_header_and_units() -> None:
    text = export_dsn(_board(), name="myboard")

    assert text.startswith('(pcb "myboard"')
    assert '(string_quote ")' in text
    assert "(space_in_quoted_tokens on)" in text
    assert '(host_cad "netlist-agent")' in text
    assert "(resolution um 10)" in text
    assert "(unit um)" in text


def test_structure() -> None:
    text = export_dsn(_board())

    # Boundary from the outline bbox, in µm with y negated (y-up).
    assert "(boundary (rect pcb 95000 -110000 125000 -95000))" in text
    assert "(layer F.Cu (type signal))" in text
    assert "(layer B.Cu (type signal))" in text
    # Rule from the "Default" net class (0.3 mm / 0.25 mm).
    assert "(rule (width 300) (clearance 250))" in text
    assert '(via "Via[0-1]_800:400_um")' in text


def test_placement_groups_shared_footprints() -> None:
    text = export_dsn(_board())

    # R1 and R2 share one component block; U1 gets its own.
    assert text.count("(component R_0603") == 1
    assert text.count("(component SOIC-8") == 1
    assert "(place R1 100000 -100000 front 0)" in text
    assert "(place R2 110000 -100000 front 0)" in text
    assert "(place U1 120000 -105000 front 90)" in text
    r1 = text.index("(place R1")
    r2 = text.index("(place R2")
    assert text.rindex("(component R_0603") < r1 < r2 < text.index("(component SOIC-8")


def test_image_pins_undo_footprint_rotation() -> None:
    text = export_dsn(_board())

    # Unrotated footprint: offsets are simply world - origin (y negated).
    assert "(pin Round[A]800 1 -750 0)" in text
    assert "(pin Round[A]800 2 750 0)" in text
    # Rotated footprint: world offsets (0.5, -1.0) / (0.5, 1.0) un-rotate by
    # 90 degrees back to the local pad positions (1.0, 0.5) / (-1.0, 0.5).
    assert "(pin Round[A]800 1 1000 -500)" in text
    assert "(pin Round[A]800 2 -1000 -500)" in text
    # One image per distinct footprint name, plus the shared padstack.
    assert text.count("(image ") == 2
    assert "(padstack Round[A]800" in text
    assert "(shape (circle F.Cu 800))" in text
    assert "(shape (circle B.Cu 800))" in text


def test_network_quotes_net_names() -> None:
    text = export_dsn(_board())

    assert "(pins R1-1 R2-1 U1-1)" in text
    assert "(pins R1-2 R2-2 U1-2)" in text
    # The name with a space is quoted with the declared string_quote and, as
    # nothing else references net 2, appears exactly once.
    assert text.count('"NET 2"') == 1
    assert "(net GND" in text


def test_wiring() -> None:
    text = export_dsn(_board())

    assert "(wire (path F.Cu 250 99250 -100000 109250 -100000) (net GND) (type route))" in text
    assert '(via "Via[0-1]_800:400_um" 105000 -100000 (net GND))' in text


def test_deterministic_and_balanced() -> None:
    board = _board()
    text = export_dsn(board)

    assert text == export_dsn(_board())
    assert text.count("(") == text.count(")")


def test_write_dsn(tmp_path: Path) -> None:
    output = tmp_path / "board.dsn"
    write_dsn(_board(), output, name="myboard")

    assert output.read_text(encoding="utf-8") == export_dsn(_board(), name="myboard")
