from pathlib import Path

import pytest

from netlist_agent.kicad_pcb import parse_board
from netlist_agent.ratsnest import compute_ratsnest
from netlist_agent.ses import apply_ses, parse_ses

BOARD = """
(kicad_pcb (version 20221018) (generator test)
  (net 0 "")
  (net 1 "SIG")
  (net 2 "/GND")
  (footprint "test:R" (at 100 100) (property "Reference" "R1")
    (pad "1" smd rect (at 0 0) (size 0.8 0.8) (net 1 "SIG")))
  (footprint "test:R" (at 110 100) (property "Reference" "R2")
    (pad "1" smd rect (at 0 0) (size 0.8 0.8) (net 1 "SIG")))
)
"""

# freerouting-style session: µm resolution, y-up (board y-down negated).
SES = """
(session "board.ses"
  (routes
    (resolution um 10)
    (parser (host_cad "netlist-agent"))
    (network_out
      (net "SIG"
        (wire (path F.Cu 2500 1000000 -1000000 1050000 -950000 1100000 -1000000))
        (via "Via[0-1]_800:400_um" 1050000 -950000)
      )
      (net "GND"
        (wire (path B.Cu 5000 1000000 -1020000 1100000 -1020000))
      )
      (net "MYSTERY"
        (wire (path F.Cu 2500 900000 -900000 910000 -900000))
      )
    )
  )
)
"""


@pytest.fixture()
def files(tmp_path: Path) -> tuple[Path, Path]:
    board = tmp_path / "board.kicad_pcb"
    board.write_text(BOARD.strip(), encoding="utf-8")
    ses = tmp_path / "board.ses"
    ses.write_text(SES.strip(), encoding="utf-8")
    return board, ses


def test_parse_ses_conversion(files: tuple[Path, Path]) -> None:
    board_file, ses_file = files
    routes = parse_ses(ses_file, parse_board(board_file))

    sig = [s for s in routes.segments if s.net_code == 1]
    assert len(sig) == 2  # 3-point path -> 2 segments
    assert (sig[0].x1, sig[0].y1) == (100.0, 100.0)  # y negated back to y-down
    assert (sig[0].x2, sig[0].y2) == (105.0, 95.0)
    assert sig[0].width == pytest.approx(0.25)
    assert sig[0].layer == "F.Cu"

    # "/GND" board net matches the SES's unprefixed "GND".
    gnd = [s for s in routes.segments if s.net_code == 2]
    assert len(gnd) == 1
    assert gnd[0].width == pytest.approx(0.5)
    assert gnd[0].layer == "B.Cu"

    # Unknown net imports with net_code 0.
    mystery = [s for s in routes.segments if s.net_code == 0]
    assert len(mystery) == 1

    assert len(routes.vias) == 1
    via = routes.vias[0]
    assert (via.x, via.y, via.net_code) == (105.0, 95.0, 1)


def test_apply_ses_round_trips(files: tuple[Path, Path]) -> None:
    board_file, ses_file = files
    output = board_file.parent / "routed.kicad_pcb"
    routes = apply_ses(board_file, ses_file, output)

    parsed = parse_board(output)
    assert len(parsed.segments) == len(routes.segments)
    assert len(parsed.vias) == len(routes.vias)

    after = compute_ratsnest(parsed)
    sig = next(n for n in after.nets if n.net_code == 1)
    assert sig.fully_routed  # imported path connects R1 to R2


def test_empty_session(tmp_path: Path, files: tuple[Path, Path]) -> None:
    board_file, _ = files
    empty = tmp_path / "empty.ses"
    empty.write_text('(session "x" (placement))', encoding="utf-8")
    routes = parse_ses(empty, parse_board(board_file))
    assert routes.segments == []
    assert routes.vias == []


def test_resolution_mil_and_mm_with_divisor(tmp_path: Path, files: tuple[Path, Path]) -> None:
    board_file, _ = files
    board = parse_board(board_file)

    # (resolution mil 10): 10 units per mil, 1 mil = 0.0254 mm.
    mil = tmp_path / "mil.ses"
    mil.write_text(
        '(session "x" (routes (resolution mil 10) (network_out\n'
        '  (net "SIG" (wire (path F.Cu 100 10000 -10000 20000 -10000))))))',
        encoding="utf-8",
    )
    routes = parse_ses(mil, board)
    assert len(routes.segments) == 1
    seg = routes.segments[0]
    assert (seg.x1, seg.y1, seg.x2, seg.y2) == (25.4, 25.4, 50.8, 25.4)
    assert seg.width == pytest.approx(0.254)

    # (resolution mm 10): 10 units per mm.
    mm = tmp_path / "mm.ses"
    mm.write_text(
        '(session "x" (routes (resolution mm 10) (network_out\n'
        '  (net "SIG" (wire (path F.Cu 2 1050 -950 1100 -950))))))',
        encoding="utf-8",
    )
    routes = parse_ses(mm, board)
    assert len(routes.segments) == 1
    seg = routes.segments[0]
    assert (seg.x1, seg.y1, seg.x2, seg.y2) == (105.0, 95.0, 110.0, 95.0)
    assert seg.width == pytest.approx(0.2)


# Deliberately messy session: mil/10 resolution, (type protect) siblings,
# multiple paths per wire, numeric layer ids, decimal coordinates, numeric
# net atoms (name match and code fallback), and malformed entries.
MESSY_BOARD = """
(kicad_pcb (version 20221018) (generator test)
  (net 0 "")
  (net 1 "SIG")
  (net 2 "/GND")
  (net 5 "3")
)
"""

MESSY_SES = """
(session "messy.ses"
  (routes
    (resolution mil 10)
    (network_out
      (net "SIG"
        (wire (path 1 100 10000 -10000 10000.5 -10010) (type protect))
        (wire
          (path F.Cu 100 0 0 3937 0)
          (path B.Cu 200 0 -1000 3937 -1000)
        )
        (wire (type protect))
        (via "Via[0-1]_800:400_um")
      )
      (net 3
        (wire (path F.Cu 100 1000 -1000 2000 -1000))
      )
      (net 2
        (via "Via[0-1]_800:400_um" 1000 -1000)
      )
      (net 7
        (wire (path F.Cu 100 0 0 100 0))
      )
      (net)
      (net "EMPTY")
    )
  )
)
"""


def test_messy_session(tmp_path: Path) -> None:
    board_file = tmp_path / "messy.kicad_pcb"
    board_file.write_text(MESSY_BOARD.strip(), encoding="utf-8")
    ses_file = tmp_path / "messy.ses"
    ses_file.write_text(MESSY_SES.strip(), encoding="utf-8")

    routes = parse_ses(ses_file, parse_board(board_file))
    assert len(routes.segments) == 5
    assert len(routes.vias) == 1

    # "SIG": one segment from the protected wire, two from the two-path wire;
    # the pathless wire and the coordinate-less via are skipped.
    sig = [s for s in routes.segments if s.net_code == 1]
    assert len(sig) == 3
    protected = sig[0]
    assert protected.layer == "1"  # numeric layer id kept as string
    assert protected.width == pytest.approx(0.254)
    assert (protected.x1, protected.y1) == (25.4, 25.4)
    assert (protected.x2, protected.y2) == (25.4013, 25.4254)  # decimal coord
    assert {s.layer for s in sig[1:]} == {"F.Cu", "B.Cu"}

    # (net 3 ...): "3" matches the board net *named* "3" (code 5) by name.
    named = [s for s in routes.segments if s.net_code == 5]
    assert len(named) == 1
    assert (named[0].x1, named[0].y1, named[0].x2, named[0].y2) == (2.54, 2.54, 5.08, 2.54)

    # (net 2 ...): no board net named "2", so 2 is used as the net code.
    via = routes.vias[0]
    assert (via.x, via.y, via.net_code) == (2.54, 2.54, 2)

    # (net 7 ...): neither a name nor a known code -> falls back to 0.
    unknown = [s for s in routes.segments if s.net_code == 0]
    assert len(unknown) == 1
