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
