"""Tests for net-class parsing and lookup helpers in netlist_agent.kicad_pcb."""

from __future__ import annotations

from pathlib import Path

import pytest

from netlist_agent.kicad_pcb import clearance_for_net, net_class_for, parse_board, width_for_net

# /VCC and GND belong to "Power"; GND is also listed in "Default" (the
# explicit class must win). SIG is only in "Default"; FLOAT is in no class.
BOARD = """
(kicad_pcb (version 20221018) (generator test)
  (net 0 "")
  (net 1 "/VCC")
  (net 2 "GND")
  (net 3 "SIG")
  (net 4 "FLOAT")
  (net_class "Power" "High-current nets" (clearance 0.3) (trace_width 0.5)
    (add_net "VCC")
    (add_net "GND")
  )
  (net_class "Default" (clearance 0.2) (trace_width 0.3)
    (add_net "/SIG")
    (add_net "GND")
  )
)
"""

# No "Default" class; "Sensitive" sets a clearance but no trace_width.
BOARD_NO_DEFAULT = """
(kicad_pcb (version 20221018) (generator test)
  (net 1 "SHIELD")
  (net 2 "FLOAT")
  (net_class "Sensitive" (clearance 0.6) (add_net "SHIELD"))
)
"""


@pytest.fixture()
def board_file(tmp_path: Path) -> Path:
    path = tmp_path / "netclass.kicad_pcb"
    path.write_text(BOARD.strip(), encoding="utf-8")
    return path


@pytest.fixture()
def no_default_file(tmp_path: Path) -> Path:
    path = tmp_path / "no_default.kicad_pcb"
    path.write_text(BOARD_NO_DEFAULT.strip(), encoding="utf-8")
    return path


def test_parse_net_classes(board_file: Path) -> None:
    board = parse_board(board_file)
    assert set(board.net_classes) == {"Power", "Default"}
    power = board.net_classes["Power"]
    assert power.name == "Power"  # description string is skipped, not taken as the name
    assert power.clearance == 0.3
    assert power.trace_width == 0.5
    assert power.nets == ["VCC", "GND"]
    assert board.net_classes["Default"].nets == ["/SIG", "GND"]


def test_missing_clearance_and_trace_width_stay_none(no_default_file: Path) -> None:
    board = parse_board(no_default_file)
    sensitive = board.net_classes["Sensitive"]
    assert sensitive.clearance == 0.6
    assert sensitive.trace_width is None


def test_net_class_for_explicit_and_default(board_file: Path) -> None:
    board = parse_board(board_file)
    assert net_class_for(board, 1).name == "Power"  # "/VCC" matches member "VCC"
    assert net_class_for(board, 2).name == "Power"  # explicit class beats "Default"
    assert net_class_for(board, 3).name == "Default"  # "SIG" matches member "/SIG"
    assert net_class_for(board, 4) is None
    assert net_class_for(board, 99) is None  # unknown net code


def test_lookup_helpers_explicit_class(board_file: Path) -> None:
    board = parse_board(board_file)
    assert width_for_net(board, 1) == 0.5
    assert clearance_for_net(board, 1) == 0.3


def test_lookup_helpers_default_class_fallback(board_file: Path) -> None:
    board = parse_board(board_file)
    assert width_for_net(board, 3) == 0.3
    assert clearance_for_net(board, 3) == 0.2
    # FLOAT is in no class: values still come from the "Default" class.
    assert width_for_net(board, 4) == 0.3
    assert clearance_for_net(board, 4) == 0.2


def test_lookup_helpers_plain_default(no_default_file: Path) -> None:
    board = parse_board(no_default_file)
    # SHIELD's class has no trace_width and there is no "Default" class.
    assert width_for_net(board, 1) == 0.25
    assert width_for_net(board, 1, default=0.4) == 0.4
    assert clearance_for_net(board, 1) == 0.6
    # FLOAT is in no class at all: plain defaults.
    assert width_for_net(board, 2) == 0.25
    assert clearance_for_net(board, 2) == 0.15
    assert clearance_for_net(board, 2, default=0.5) == 0.5
