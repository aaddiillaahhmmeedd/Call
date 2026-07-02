import math
from pathlib import Path

import pytest

from netlist_agent.kicad_pcb import Board, Pad, TrackSegment, Zone, parse_board
from netlist_agent.pour import generate_pour, write_poured_board
from netlist_agent.ratsnest import _point_in_polygon, compute_ratsnest

GND_A = (100.0, 100.0)
GND_B = (110.0, 100.0)
FOREIGN_PAD = (105.0, 100.0)
CLEARANCE = 0.3

# Net-1 copper near the pour: the F.Cu track must NOT block a B.Cu pour
# (wrong layer), the B.Cu track must (same layer).
ON_FCU_SEGMENT = (103.0, 98.0)  # on the F.Cu track, far from all other copper
ON_BCU_SEGMENT = (105.0, 102.0)


def _pad(ref: str, x: float, y: float, net_code: int, net_name: str, radius: float = 0.4) -> Pad:
    return Pad(reference=ref, pad_name="1", x=x, y=y, net_code=net_code, net_name=net_name, radius=radius)


def _board() -> Board:
    return Board(
        nets={0: "", 1: "SIG", 2: "GND"},
        pads=[
            _pad("R1", *GND_A, 2, "GND"),
            _pad("R2", *GND_B, 2, "GND"),
            _pad("U1", *FOREIGN_PAD, 1, "SIG"),
        ],
        segments=[
            TrackSegment(x1=102.0, y1=98.0, x2=108.0, y2=98.0, width=0.25, layer="F.Cu", net_code=1),
            TrackSegment(x1=102.0, y1=102.0, x2=108.0, y2=102.0, width=0.25, layer="B.Cu", net_code=1),
        ],
    )


@pytest.fixture(scope="module")
def zone() -> Zone:
    return generate_pour(_board(), net_code=2, layer="B.Cu", clearance=CLEARANCE)


def _inside(zone: Zone, x: float, y: float) -> bool:
    return any(_point_in_polygon(x, y, polygon) for polygon in zone.polygons)


def test_pour_covers_gnd_pads(zone: Zone) -> None:
    assert zone.net_code == 2
    assert zone.layer == "B.Cu"
    assert zone.polygons
    assert _inside(zone, *GND_A)
    assert _inside(zone, *GND_B)


def test_pour_clears_foreign_pad(zone: Zone) -> None:
    assert not _inside(zone, *FOREIGN_PAD)
    # Every point within `clearance` of the foreign pad center stays clear too.
    for angle in range(0, 360, 45):
        x = FOREIGN_PAD[0] + CLEARANCE * math.cos(math.radians(angle))
        y = FOREIGN_PAD[1] + CLEARANCE * math.sin(math.radians(angle))
        assert not _inside(zone, x, y)


def test_same_layer_segment_blocks_other_layer_does_not(zone: Zone) -> None:
    assert not _inside(zone, *ON_BCU_SEGMENT)  # B.Cu track blocks the B.Cu pour
    assert _inside(zone, *ON_FCU_SEGMENT)  # F.Cu track is on the wrong layer to block


def test_pour_connects_gnd_pads(zone: Zone) -> None:
    board = _board()
    before = compute_ratsnest(board)
    gnd_before = next(n for n in before.nets if n.net_code == 2)
    assert gnd_before.cluster_count == 2  # two GND pads, no copper between them

    board.zones.append(zone)
    after = compute_ratsnest(board)
    gnd_after = next(n for n in after.nets if n.net_code == 2)
    assert gnd_after.cluster_count < gnd_before.cluster_count
    assert gnd_after.fully_routed


def test_write_poured_board_parses(zone: Zone, tmp_path: Path) -> None:
    source = tmp_path / "mini.kicad_pcb"
    source.write_text(
        '(kicad_pcb (version 20221018) (generator test)\n'
        '  (net 0 "")\n'
        '  (net 1 "SIG")\n'
        '  (net 2 "GND")\n'
        ')\n',
        encoding="utf-8",
    )

    output = tmp_path / "poured.kicad_pcb"
    write_poured_board(source, [zone], output)

    parsed = parse_board(output)
    assert len(parsed.zones) == 1
    written = parsed.zones[0]
    assert written.net_code == 2
    assert written.layer == "B.Cu"
    assert written.polygons == zone.polygons


def _inside(zone: Zone, x: float, y: float) -> bool:
    return any(_point_in_polygon(x, y, poly) for poly in zone.polygons)


def _gnd_only_board() -> Board:
    return Board(
        nets={0: "", 2: "GND"},
        pads=[_pad("R1", *GND_A, 2, "GND"), _pad("R2", *GND_B, 2, "GND")],
    )


def test_thermal_reliefs() -> None:
    board = _gnd_only_board()
    zone = generate_pour(board, 2, thermal=True, spoke_width=0.3)

    # Pad center and axis-aligned spoke points stay connected to the pour.
    assert _inside(zone, *GND_A)
    ring = (0.4 + CLEARANCE) / 2  # halfway through the clearance ring
    for dx, dy in ((ring, 0.0), (-ring, 0.0), (0.0, ring), (0.0, -ring)):
        assert _inside(zone, GND_A[0] + dx, GND_A[1] + dy), f"spoke at ({dx}, {dy}) missing"

    # Diagonal points inside the clearance ring are relieved (no copper).
    diag = ring / math.sqrt(2)
    for sx, sy in ((diag, diag), (-diag, diag), (diag, -diag), (-diag, -diag)):
        assert not _inside(zone, GND_A[0] + sx, GND_A[1] + sy), f"ring at ({sx}, {sy}) not relieved"

    # Connectivity through the spokes still closes the net.
    board.zones.append(zone)
    gnd = next(n for n in compute_ratsnest(board).nets if n.net_code == 2)
    assert gnd.fully_routed


def test_smooth_traces_single_outline() -> None:
    zone = generate_pour(_gnd_only_board(), 2, smooth=True)

    assert len(zone.polygons) == 1  # one hole-free region -> one traced ring
    polygon = zone.polygons[0]
    assert len(polygon) >= 4
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        assert x1 == x2 or y1 == y2, "smooth outline must stay rectilinear"
    assert _inside(zone, *GND_A)
    assert _inside(zone, *GND_B)


def test_smooth_falls_back_on_holes() -> None:
    board = _gnd_only_board()
    board.pads.append(_pad("U1", *FOREIGN_PAD, 1, "SIG"))
    board.nets[1] = "SIG"
    zone = generate_pour(board, 2, smooth=True)

    # The foreign-pad clearance hole forces the rectangle decomposition.
    assert len(zone.polygons) > 1
    assert all(len(poly) == 4 for poly in zone.polygons)
    assert not _inside(zone, *FOREIGN_PAD)
    ring = (0.4 + CLEARANCE) / 2  # halfway through the clearance ring
    assert not _inside(zone, FOREIGN_PAD[0] + ring, FOREIGN_PAD[1])
    assert _inside(zone, *GND_A)
